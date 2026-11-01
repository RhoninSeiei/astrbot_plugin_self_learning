# 定时知识摄取与结构化分析修复

基于上游 4.4.5、提交 `4544b08`；修复仅涉及 self-learning 插件，不修改 AstrBot 或 LightRAG 库。分支 `dev/lightrag-scheduled-ingestion`，自用 fork 为 https://github.com/RhoninSeiei/astrbot_plugin_self_learning 。

## 定时知识摄取

渐进学习的前台及后台批次，在筛选消息后、执行风格学习前，将消息交给 V2 的知识专用入口。不会调用整个实时流程，也不会开启本地 Mem0 或改变 LivingMemory 委托。已有实时开关和六小时学习周期保持原配置。

LightRAG manager 使用原生 `doc-` 内容哈希，兼容之前 `ainsert(text)` 的标识；定时数据库 ID 与实时平台 ID 不参与去重。空昵称统一回退发送者 ID。同群摄取串行化，状态持久化跨热重载保留。

- `processed` 直接跳过，不再 enqueue 或抽取。
- 不存在的文档提交一次；已有 pending 等状态只恢复原队列。
- `failed` 保留原文档、报告失败，不自动重新提交或改写状态，仍需定位实际提取错误后使用库支持的恢复方式。
- 只有文档与状态已可靠保存，才能交接给风格批次。没有持久化记录或落盘失败时保留 raw 未处理状态。
- 风格成功不代表知识成功，批次日志单独记录知识完成、重复、跳过、待处理、失败数量。

空批次仍恢复已有 pending；插件启动时扫描持久化群目录，按现有白名单和黑名单恢复，不依赖新消息阈值。没有回填已被旧风格批次标记为处理完成的全部历史消息；避免隐式大规模重摄取。

修复真实库群隔离问题：旧代码只设置不同 working_dir，仍共享空 workspace 的内存状态。现在使用 `working_dir=.../lightrag`、`workspace=group_id`，各存储仍落在原有 `.../lightrag/group_id` 目录，无需迁移文件。真实离线测试修复前复现第二群误判重复，修复后两群分别生成图谱。

渐进学习持有真正的循环 task，并在停止时取消、等待；取消回调不会误读 cancelled task 的 exception。停止与新启动使用同一锁，避免旧实例与新实例同时写存储。

## JSON 分析

初始目标、阶段规划、意图分析改用固定分析 system_prompt；群聊文本、历史及当前目标通过 JSON 数据放入用户内容。移除用于聊天风格的隐藏指令包装和响应泄漏消毒，不把用户文本插入系统指令。

有效 JSON 直接解析，思考标签及代码围栏兼容；无结构或空响应直接降级，有损坏结构只进行一次本地 Guardrails 修复，无额外模型重试，不假设 Provider 支持 response_format。可选目标类型与主题只接受字符串，异常容器值降级为 None。

## 验证

- 当前生产镜像，无网络、无生产挂载：126 passed、1 skipped、9 subtests passed。覆盖摄取并发去重、持久化重建、pending恢复、failed不重复提交、落盘失败、群过滤、生命周期、JSON正常和降级、原有委托/指标/触发器。
- 真实 LightRAG 存储，合成文本、本地模拟 LLM/embedding/tokenizer：图谱落盘、同群去重、双群隔离、重建manager去重全部通过。没有调用外部模型。
- `tests/integration/run_focused_regressions.py` 兼容已有测试的旧包名；`tests/integration/lightrag_offline_smoke.py` 为显式离线集成检查。
- 现网实际模型的知识质量及下一次定时产出仍需自然任务日志验证；初始化、预热或模拟模型通过不等于现网模型已完成抽取。

## 上线记录

2026-11-01 05:57 UTC，定向热重载 self-learning 一次。维护记录 `self-learning-release-20261101-20261101T055755Z.json` 校验通过：六个部署源码文件匹配、目标唯一活动、配置及其他插件文件不变。核心校验值与 PID 1 启动标识保持原值。备份保留至 2026-11-15。发布前最近两次定时批次均已有完成日志，未在旧定时写入过程中切换插件。
