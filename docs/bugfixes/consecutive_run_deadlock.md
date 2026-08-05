# 连续运行脚本死锁问题修复

## 问题现象

在同一 terminal 连续执行两个回测脚本时，第二个脚本卡死：

```bash
python tests/run_simulation.py   # 成功完成
python tests/test_strategy.py     # 卡住，最终超时报错
```

报错信息：
```
运行报错: Actor initialization timed out after 30s!
```

## 根因分析

### 死锁位置

卡住发生在 `TrackerActor._start()` 初始化阶段。日志显示 `register` 和 `get_position` 都成功，但 `_start()` 超时。

### 死锁原理

`TrackerActor._start()` 是一个 `async` 方法，运行在 `GlobalAsyncLoop` 线程（`self._loop`）上。它在循环中调用了同步阻塞方法 `self.asset_cache.get_cache_info(sid, self._loop)`：

```python
# cache.pyx - get_cache_info 内部实现
asyncio.run_coroutine_threadsafe(self._async_fetch(sid), loop).result()  # 阻塞！
```

`.result()` 会阻塞当前线程等待协程完成。但当前线程就是 loop 线程本身，loop 被阻塞后无法执行 `_async_fetch` 协程，形成**经典死锁**：

```
loop 线程: _start() → get_cache_info → .result() 阻塞等待
         ↓ (loop 被阻塞，无法调度)
loop 队列: _async_fetch 协程等待执行（永远不会被调度）
```

### 为什么第一次运行不卡死

第一次运行 `run_simulation.py` 时，数据库为空（刚清空），`get_position()` 返回 0 条记录：

```python
datas = await async_gt.get_position()  # 返回 []
for row in datas:                       # 循环体不执行
    asset_core = self.asset_cache.get_cache_info(sid, self._loop)  # 不会被调用
```

第二次运行 `test_strategy.py` 时，数据库里有第一次运行残留的 position（1 条），循环体执行，触发 `get_cache_info` → 死锁。

## 修复方案

### 核心修复（`simulate.pyx`）

在 `TrackerActor._start()` 中，不调用同步的 `get_cache_info`（它会阻塞 loop），而是直接 `await` 异步版本：

```python
# bt_core/execution/core/finance/simulate.pyx - TrackerActor._start()

# 修复前（死锁）：
asset_core = self.asset_cache.get_cache_info(sid, self._loop)

# 修复后（直接 await，不阻塞 loop）：
if sid not in self.asset_cache._c_cache:
    await self.asset_cache._async_fetch(sid)
asset_core = self.asset_cache._c_cache.get(sid, None)
```

`_async_fetch` 在当前 async 上下文里直接 `await`，不需要 `run_coroutine_threadsafe` + `.result()`，不会死锁。

### 修复原理对比

| 调用方式 | 是否死锁 | 原因 |
|---------|---------|------|
| `get_cache_info(sid, loop)` | 死锁 | 内部 `.result()` 阻塞 loop 线程 |
| `await _async_fetch(sid)` | 安全 | 在当前 async 上下文直接 await |

## 验证

修复后连续运行两个脚本不再卡住：
```bash
cd tests && python run_simulation.py && python test_strategy.py
```

## 注意事项

- `get_cache_info` 的同步阻塞模式仍然保留，供非 loop 线程的场景使用（如 `process_order`、`set_cash`，这些方法从主线程调用，通过 `run_coroutine_threadsafe` 提交到 loop 线程）
- 只有 `_start()` 这种**本身就在 loop 线程上运行的 async 方法**才需要改为直接 `await`
- 修改 `.pyx` 文件后需要重新编译：`poetry run python setup.py build_ext --inplace`