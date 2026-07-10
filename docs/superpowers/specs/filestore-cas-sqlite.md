# FileStore 重构：SQLite 索引 + 内容寻址存储 (CAS)

## 1. 目标与范围

把 `python/bookscout-filestore` 从「key 路径 + `.metadata.json` 旁车」模型重构为：

- **内容寻址存储 (Content-Addressed Storage, CAS)**：物理 blob 按 sha256 分片存放；key 变为虚拟路径，仅存在于索引 DB。
- **索引 DB（SQLite）**：通过组合持有 `bookscout-sqlite` 的 `SQLite` 实例管理。DB 本质是文件索引 + 元数据。
- **生命周期**：继承 `AsyncResourceMixin`，`shutdown` 只释放引擎，**不删除** DB 文件。
- **能力扩展**：checksum、查重去重、校验、按 key FTS5 查询、目录/文件列表、reconcile 索引。
- **接口稳定**：`BlobStore` Protocol 签名尽量不改；新增能力挂在 `FileStore` 上。

影响文件：
- `python/bookscout-filestore/bookscout/filestore/__init__.py`（重写）
- `python/bookscout-filestore/bookscout/filestore/exceptions.py`（新增异常子类）
- `python/bookscout-filestore/pyproject.toml`（依赖 bookscout-sqlite、SQLModel）
- `python/bookscout-core/bookscout/core/types.py`（`BlobStore` Protocol——本次不改签名，仅核对实现符合性）

## 2. 决策记录（已与用户确认）

| 决策 | 选择 |
|---|---|
| dedup 存储模型 | 内容寻址存储 (CAS) |
| 启动行为 | 每次启动都 reconcile（FS↔DB 对账） |
| 与 bookscout-sqlite 关系 | 组合持有 `SQLite` 实例 |
| legacy 文件摄入 | 启动时把松散文件移动进 `blobs/` 分片布局 |
| Protocol 改动 | `BlobStore` 签名保持不变；新增能力仅放在 `FileStore` |

## 3. 物理布局

```
<base_path>/
├── blobs/<hh>/<full_sha256>      # 内容 blob，按 sha256 前 2 位分片
├── tmp/                          # multipart 上传暂存
└── index.db                      # SQLite 索引（SQLite 实例管理）
```

- `base_path` 启动时若缺失则创建（→ 空 DB）。
- key 不再对应物理路径，仅是 `file_index` 表主键（如 `books/epub/foo.epub`）。
- `copy` 仅插入新 key→hash 映射行，零物理拷贝。
- `delete` 删行；若该 hash 无任何 key 引用，则 unlink blob（引用计数用 `COUNT(*) GROUP BY content_hash` 派生，无需冗余列）。

## 4. SQLite 索引 schema（SQLModel）

```python
class FileIndex(SQLModel, table=True):
    __tablename__ = "file_index"

    key:          str   = Field(primary_key=True)          # 虚拟路径
    content_hash: str   = Field(index=True)                # sha256 hex → 定位 blob
    size:         int                                        # 字节数
    created_at:   float = Field(default_factory=time.time)  # epoch 秒
    modified_at:  float = Field(default_factory=time.time)
    metadata_:    str | None = Field(default=None, alias="metadata", sa_column=Column("metadata", JSON))
    # 用户 metadata，JSON 列，替代 .metadata.json 旁车
```

FTS5（外部内容表，列 `key`，对 `file_index.key` 建全文索引）：

```sql
CREATE VIRTUAL TABLE file_index_fts USING fts5(
    key, content='file_index', content_rowid='rowid'
);
-- 触发器保持同步：INSERT/UPDATE(key)/DELETE 三类 trigger
```

DDL + 触发器在 `startup` 阶段用 `SQLite.exec` 原始 SQL 创建（FTS5 与触发器非 SQLModel 声明式）。

## 5. 方法语义（签名保留，行为转 CAS）

| 方法 | 重构后行为 |
|---|---|
| `upload(data, key, metadata=None)` | 流式 hash 数据 → 写 blob（不存在才写，幂等）→ upsert `file_index`。返回 `key`。 |
| `upload_multipart(parts, key, metadata=None)` | 流式写 `tmp/<uuid>` 并增量 hash → 提升为 blob → upsert 行 → 删 tmp。 |
| `download(key, *, stream=False, chunk_size=..., verify=False)` | 行→hash→blob；`verify=True` 时重算 hash 比对，不符抛 `FetchError`。 |
| `get_metadata(key)` | `SELECT metadata FROM file_index WHERE key=:k`，缺失返回 `{}`。 |
| `delete(key)` | 删行；若该 hash 无引用则 unlink blob。 |
| `list(prefix, page_size=10)` | 分页 `SELECT key FROM file_index WHERE key LIKE :p||'%'`（替代 rglob）。 |
| `exists(key)` | DB 行存在。 |
| `clear(prefix='')` | 删匹配 prefix 的所有行；GC 无引用 blob。 |
| `copy(source_key, dest_key)` | insert `dest_key` 行指向同 hash。近零成本。 |

`BlobStore` Protocol 签名保持不变；`download` 新增的 `verify` 走 `**kwargs` 兼容。

## 6. FileStore 扩展方法（非 Protocol）

- `index()` — **reconcile**，启动时自动跑一次，也可手动调：
  1. **完整性校验**：遍历 `blobs/`，重算每个 blob 的 sha256 与文件名比对；不符则移入 `quarantine/` 并记日志。
  2. **悬空 key 清理**：删掉 blob 已丢失的 `file_index` 行。
  3. **legacy 摄入**：`base_path` 下不在 `blobs//tmp//index.db//quarantine/` 的松散文件，按相对路径作 key，hash 后**移动**进 `blobs/` 并索引（原路径消失）。
- `verify(key=None)` — 重算 blob hash，返回不一致列表（校验）。`key=None` 校验全部。
- `find_duplicates()` — `SELECT content_hash, COUNT(*) ... GROUP BY content_hash HAVING COUNT>1`，返回重复内容组（查重）。
- `find_by_checksum(hash)` — 返回指向该 hash 的所有 key。
- `list_dir(prefix='')` — 虚拟目录列举：返回 `DirEntry(name, is_dir, key)` 直接子项列表。
- `search(query)` — 对 `key` 跑 FTS5 查询，返回匹配 key 列表。

## 7. 生命周期

```python
class FileStore(LoggingMixin, AsyncResourceMixin):
    def __init__(self, logger, config: FileStoreConfig): ...
        # 创建 SQLite(config=SQLiteConfig(uri="sqlite+aiosqlite:///<base_path>/index.db"), logger=logger)
        # 但不 startup；startup 在 FileStore.startup 中触发

    async def startup(self) -> None:
        # 1. ensure base_path / blobs / tmp / quarantine
        # 2. await self._sqlite.startup()
        # 3. await self._sqlite.create_all([FileIndex])
        # 4. exec FTS5 DDL + 同步触发器
        # 5. await self.index()   # reconcile
        # 6. await super().startup()

    async def shutdown(self) -> None:
        await self._sqlite.shutdown()   # dispose engine；index.db 文件保留
```

`SQLite` 实例的 `exec`/`session` 直接复用，无需在 FileStore 内重造会话管理。

## 8. 配置

```python
class FileStoreConfig(BaseModel):
    type: t.Literal["filesystem"] = Field(default="filesystem")
    base_path: os.PathLike[str] | str = Field(default="/store")
    # 新增（可选）
    index_db_name: str = Field(default="index.db")
    shard_depth: int = Field(default=2, ge=1, le=4)  # blobs 分片前缀位数
    fts: bool = Field(default=True)                   # 是否建 FTS5
```

## 9. 异常

`exceptions.py` 新增：
- `IndexError(FileStoreError)` — reconcile / DDL 失败。
- `IntegrityError(FileStoreError)` — checksum 校验失败（verify/download verify 不符）。
- `ConflictError(FileStoreError)` — key 已存在等冲突（按需）。

复用现有 `UploadError/FetchError/DownloadError/DeleteError/CopyError` 与 `handle_errors` 装饰器。

## 10. 校验与 TDD 计划

测试覆盖（`tests/test_filestore.py`，新文件）：
1. 空 base_path 启动 → 创建目录 + 空 DB。
2. upload → blob 按 hash 落盘 + 行存在 + get_metadata 正确。
3. 相同内容不同 key → 同一 blob（CAS 去重）。
4. copy → 零物理拷贝，两 key 指向同 hash。
5. delete 一个 key → blob 仍在；删全部引用 → blob 被回收。
6. verify 全通过；篡改 blob → verify 报 IntegrityError。
7. legacy 摄入：预放松散文件启动后被移入 blobs 并索引。
8. reconcile 修复悬空 key / 损坏 blob（移入 quarantine）。
9. FTS5 search 命中。
10. list / list_dir / find_duplicates 行为。

实现遵循 TDD：先写失败测试，再实现。

## 11. 实现步骤（执行计划）

1. 改 `pyproject.toml`：依赖 `bookscout-sqlite`（workspace）、`sqlmodel`、`aiosqlite`（透传）、`aiofiles`。
2. 写 `exceptions.py` 新增类。
3. 写模型模块 `models.py`（`FileIndex` SQLModel + FTS DDL/触发器 SQL 常量）。
4. 重写 `__init__.py`：`FileStoreConfig`、`FileStore`（CAS + SQLite 组合 + lifecycle + 扩展方法）。
5. 写测试 `tests/test_filestore.py`，逐项红→绿。
6. 跑 ruff/mypy/pytest 全绿。
7. 核对 `BlobStore` Protocol：实现符合即可，不改签名（除非后续需求要求）。

## 12. 非目标 / 风险

- 不做分布式锁、不做并发写 blob 的细粒度锁（单进程 async 足够）。
- FTS5 分词用默认 unicode61；中文 key 分词有限，可后续换 tokenizer。
- CAS 下 key 不再映射到直观物理路径，外部工具直接读 FS 会失效——可接受，接口层不暴露物理布局。
