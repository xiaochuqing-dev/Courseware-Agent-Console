# Courseware Agent Console 差异安全审查

## 执行摘要

| 严重级别 | 未解决数量 |
|---|---:|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 1 |

总体风险：低。

建议：APPROVE。用户已完成本地验收并明确授权在现有 main 分支提交、推送，不另建分支。

关键指标：

- 基线：ce3543b16edbc4476677acb8552ac1e119bb67c6。
- 代码库规模：74 个 Python 文件，采用 FOCUSED 审查。
- 高风险路径：安全回收、工作流任务归档与回滚、任务快照校验。
- 高风险生产入口均有自动化测试。
- 当前全量测试：162 passed。
- 安全回归：未发现删除既有安全校验、重新引入永久删除或绕过任务快照的情况。

## 变更范围

主要生产变更：

| 文件 | 风险 | 说明 |
|---|---|---|
| services/feedback_service.py | High | 新增已保存反馈安全回收、哈希和路径身份校验 |
| services/workflow_optimization_service.py | High | 统一输入、任务快照、归档、回滚、重新验证 |
| services/task_service.py | Medium | 空轮次和系统批量说明边界 |
| services/prompt_service.py | Medium | 工作流复制指令前重新验证 |
| ui/pages/home_page.py | Medium | 删除确认、刷新、任务失效状态联动 |
| ui/pages/workflow_optimization_page.py | Medium | 三类输入合并单页、共享任务状态和组合生成 |
| ui/pages/create_project_page.py | Medium | 无竖线结构化映射、草稿删除确认和稳定绑定清理 |
| ui/widgets/feedback_drop.py | Low | 已保存材料删除入口和系统说明标记 |
| ui/widgets/glass_check_box.py | Low | 原批量复选框等价抽取复用 |

历史上下文：

- 手动工作流任务最初由 57d6255 引入。
- 任务快照、反馈上下文验证和批量复选框由 ce3543b 引入。
- 本轮没有删除 ce3543b 中的哈希、快照、批次、输出身份或复制前校验；新增逻辑继续复用这些边界。

## 高风险代码分析

### FeedbackService.recycle_saved_item

位置：services/feedback_service.py:129

生产调用者：1 个，ui/pages/home_page.py:1550。Blast radius 低。

保持的不变量：

- UI 不直接 unlink。
- 只能操作当前项目指定反馈轮次根目录中的普通文件。
- 项目、轮次、目标和批量说明边界必须匹配。
- 删除前身份与 SHA-256 必须和页面扫描结果一致。
- 回收失败时原文件必须保留。
- 删除最后一个材料不能删除轮次目录。

攻击者模型：能够在本机同一用户权限下修改项目目录的进程或用户。

已验证攻击路径：

1. 传入项目外文件：target.parent 与 round_root 不同，拒绝。
2. 传入符号链接：解析前拒绝。
3. 文件在确认后被改写：SHA-256 和文件身份复核失败，拒绝。
4. 伪造系统批量说明名称：只有严格批次 ID 正则会被保护，相似普通文件仍可正常管理。
5. Send2Trash 抛错或未移动文件：保留或恢复原文件并返回明确错误。

测试覆盖：路径逃逸、符号链接、哈希变化、回收失败、系统说明、普通相似文件、跨轮次失效、UI 取消和确认、批量指令失效。

### WorkflowOptimizationService.generate_task / validate_current_task

位置：services/workflow_optimization_service.py:213、242

生产调用者：生成 1 个，验证 3 个。Blast radius 低。

保持的不变量：

- 参考项目只能来自当前项目组的已完成项目目录。
- 参考项目只读，不在生成流程中写入。
- 用户源材料只复制，不移动、不修改。
- 新任务必须同时写入任务、快照和材料目录。
- 上一轮任务、快照、材料整体归档。
- 任一步骤失败恢复上一轮；恢复不完整时保留恢复数据。
- 复制执行指令前重算公共工具、项目、材料和任务哈希。

攻击者模型：能够选择任意本机路径或修改任务管理目录的本机用户。

已验证攻击路径：

1. 选择项目组外目录：项目 parent 不等于当前 archive_group，拒绝。
2. 材料缺失、目录冒充文件、同名冲突、保留文件名、不可读取：在替换当前任务前拒绝。
3. 快照材料文件名路径逃逸：load_current_input 拒绝绝对路径和非单文件名。
4. 管理材料目录出现符号链接、子目录或其他非普通条目：validate_current_task 判定任务过期。
5. 复制或归档阶段异常：回滚当前任务、快照和材料；回滚失败时恢复目录保留。

测试覆盖：三类输入的所有组合、空输入、非法项目、材料去重和冲突、归档、复制失败、归档失败、不完整回滚、快照路径逃逸、复制前校验、真实 GUI 组合输入和后台大文件。

## 审查中已修复的问题

### 已修复：管理材料目录忽略非文件条目

原逻辑只枚举 is_file 的条目，目录型符号链接或意外子目录可能未进入比较集合。当前改为先枚举全部直接条目，任何符号链接或非普通文件都会让任务失效。

位置：services/workflow_optimization_service.py:748。

回归测试：test_validation_rejects_non_file_entry_in_managed_material_directory。

### 已修复：系统批量说明状态重复

服务状态已经是“系统批量说明”，行组件再次拼接会显示“系统批量说明 · 系统批量说明”。当前只显示一次，不改变布局或样式。

位置：ui/widgets/feedback_drop.py:130。

回归测试：test_system_batch_note_is_marked_not_deletable_and_cannot_support_task。

### 已修复：工作流输入被拆页且小列表叠绘文字

原页面用模式切换把参考项目和人工输入拆开，项目列表较少时还会留下大段空白；同时 QListWidgetItem 自身文本与 setItemWidget 自定义行重复绘制。当前把参考项目、优化说明和补充材料合并到同一张原风格卡片中，列表按内容收缩并内部滚动，底层 item 文本改为空、仅保留可访问文本。

位置：ui/pages/workflow_optimization_page.py。

回归测试：test_unified_workflow_page_supports_real_checkbox_combination；scripts/capture_manual_workflow_screens.py；scripts/capture_phase3_screens.py。

## 未解决低风险项

### Low：Send2Trash 路径调用仍存在操作系统级 TOCTOU 窗口

位置：services/feedback_service.py:129。

说明：实现会在回收前复制安全副本，并多次核对设备、inode、大小、修改时间、路径和 SHA-256。但 Send2Trash 最终仍接受路径字符串；同一权限主体若在最后一次复核与系统回收调用之间并发替换路径，应用无法依靠纯 Path API 完全消除该窗口。

可利用性：Hard。需要本机同一用户权限、精确竞争时序和可写轮次目录。

影响：可能回收同一路径下被并发替换的条目。安全副本可用于恢复原始内容，且该风险不开放给远程或低权限用户。

建议：当前桌面单用户场景可以接受。未来如需对抗不可信本机进程，应研究 Windows 文件句柄身份锁定、IFileOperation 与 reparse point/junction 的原生校验。

## 测试覆盖与验证

- 全量 pytest：162 passed。
- smoke test：通过。
- pip check：通过。
- compileall：通过。
- 手动工作流 GUI 脚本：通过。
- 首次材料 GUI 脚本：通过。
- 反馈工作流 GUI 脚本：通过。
- 阶段三完整 GUI 脚本：通过，统一工作流页可同时显示并选择 3 个已完成项目。

基线复跑为 139 passed、1 个 GUI popup 时序失败。当前测试会先显示窗口并等待 popup 可见，再执行真实鼠标点击，消除了该误报。

## 布局与视觉回归审查

- ui/styles/app.qss 没有差异。
- GlassCheckBox 与基线 HighContrastCheckBox 的绘制、尺寸、颜色、命中区完全一致。
- 创建页仍使用原有双栏、列表容器和顶部按钮，映射内容由竖线文本改为四列对齐行。
- 首页只在原反馈材料行增加现有 quiet/iconOnly 风格的删除按钮。
- 工作流仍使用原页头、卡片、按钮属性和整体间距；仅删除模式切换并把三类可选输入合并到同一页面。
- 批量反馈布局和按钮属性未改变。

## 方法与限制

审查方法：

- 对比基线与工作区差异。
- 检查 removed validation 和历史提交来源。
- 对高风险文件读取完整实现和一跳调用者。
- 计算生产调用者和 blast radius。
- 建立路径逃逸、符号链接、哈希替换、跨轮次、批量失效和回滚攻击场景。
- 运行全量测试、GUI 脚本和依赖检查。

限制：

- 未使用独立 Windows 低权限账户进行 junction/reparse point 对抗测试。
- 未对 Send2Trash 内部 Windows 原生实现做源码级审计。
- 当前审查覆盖本地工作区，尚无提交 SHA 或远端 PR。

审查置信度：高（本地应用逻辑与测试范围）；中（操作系统回收站竞争边界）。

## 最终建议

批准提交。提交前最终复跑结果为 162 passed，smoke test、pip check、compileall、四套 GUI 验收和 git diff --check 均通过；ui/styles/app.qss 仍无差异。提交清单应包含本轮有用的源码、测试、文档和已更新验收截图，并排除 Nuitka 崩溃报告、dist、deployment 等本机构建产物。
