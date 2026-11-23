# 001 初始表结构

应用首次启动以 SQLAlchemy 模型创建表与唯一约束。现有数据表只补建缺失表，升级到修改过字段的版本需要显式迁移。

- `users` 账号角色和加盐密码摘要。
- `datasets` 不可变记录批次，以名称和 SHA-256 去重。
- `runs` 记录参数、数据批次、运行租约、分析结果、导出文件及复核决定。
- `pairing_progress` 按运行保存霉斑配对的架位级进度与已定稿配对，支撑中断续跑与并发共享。
- `schedules` 保存定时计划和下一到期时间。
- `deliveries` 保存站内分发回执，每次运行最多一个回执。
- `audit_events` 记录登录、数据入库、运行、配对、复核和报表分发动作。

## 配对进度表（pairing_progress）

- `run_id` 外键并唯一：同一次运行的并发 worker 与中断重试共享同一份配对。
- `state` 为 `running` 或 `complete`；`completed_shelves` 记录已配完的 (库房, 架位) 键。
- `pairs` 逐架位追加已定稿配对；`attempt` 记录当前租约代号，迟归旧任务代号更小，写入被拒绝。
- 全新部署由 `Base.metadata.create_all` 自动建表；已上线的库需补建本表（无字段变更）。
