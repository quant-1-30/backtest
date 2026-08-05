# bt_core 文档中心

本目录汇集了 bt_core 框架的设计文档、性能分析、A股逻辑修复与历史 Bug 修复记录。

## 📂 目录结构

```
docs/
├── README.md                          # 本文件（文档索引）
├── architecture/                      # 架构设计
│   └── ARCHITECTURE.md
├── performance/                       # 性能分析与优化
│   └── PERFORMANCE_ANALYSIS.md
├── ashare/                            # A股交易逻辑
│   ├── logic_refactor.md
│   └── bugfix_audit.md
└── bugfixes/                          # 历史 Bug 修复记录
    ├── consecutive_run_deadlock.md
    └── pnc_max_positions.md
```

---

## 📖 文档导航

### 架构设计 (`architecture/`)

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](architecture/ARCHITECTURE.md) | 框架总体架构、模块职责、类继承体系、运行时调用链路、关键数据流。新人入门必读。 |

### 性能分析 (`performance/`)

| 文档 | 说明 |
|------|------|
| [PERFORMANCE_ANALYSIS.md](performance/PERFORMANCE_ANALYSIS.md) | 基于 `bt_core/` 源码静态分析的性能卡点梳理（严重/高/中三级）与优化方案。涵盖 Indicator 全量重算 O(N²)、Feed/Resample datetime 对象分配、Analyzer snapshot 复用、LineBuffer 热路径、SHM metric 批量发布等。 |

### A股交易逻辑 (`ashare/`)

| 文档 | 说明 |
|------|------|
| [logic_refactor.md](ashare/logic_refactor.md) | `execution/core/finance/` 下 A 股交易逻辑修复重构方案（滑点、佣金、涨跌幅、限价单前视偏差等）。 |
| [bugfix_audit.md](ashare/bugfix_audit.md) | A 股交易逻辑 Bug 审计与修复记录，按 P0（资金计算错误）/ P1（交易逻辑）/ P2（代码质量）分级。 |

### 历史 Bug 修复 (`bugfixes/`)

| 文档 | 说明 |
|------|------|
| [consecutive_run_deadlock.md](bugfixes/consecutive_run_deadlock.md) | 连续运行两个回测脚本时第二个卡死（Actor 初始化超时）的死锁根因与修复。 |
| [pnc_max_positions.md](bugfixes/pnc_max_positions.md) | PNC `max_positions` 约束失效（跌停卡单导致被动超仓）的根因与修复。 |

---

## 🚀 快速入口

- **新人入门**：从 [ARCHITECTURE.md](architecture/ARCHITECTURE.md) 开始
- **性能调优**：参阅 [PERFORMANCE_ANALYSIS.md](performance/PERFORMANCE_ANALYSIS.md)
- **A股相关**：[logic_refactor.md](ashare/logic_refactor.md) + [bugfix_audit.md](ashare/bugfix_audit.md)
- **排错参考**：[consecutive_run_deadlock.md](bugfixes/consecutive_run_deadlock.md) + [pnc_max_positions.md](bugfixes/pnc_max_positions.md)