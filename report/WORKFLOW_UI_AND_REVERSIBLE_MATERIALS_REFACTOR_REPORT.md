# 工作流与可逆材料管理重构报告

## 结论

本轮实现以逻辑可靠性和可逆文件管理为主。根据用户最新验收要求，所有页面继续沿用原有布局、按钮样式、卡片透明度、背景和间距；未修改 ui/styles/app.qss，也未引入全局视觉重设计。

工作流页已删除“从项目复盘 / 人工提出优化”模式切换，把参考项目、优化说明、补充材料合并到同一页面。三类输入任一存在即可生成，也可以任意组合。已保存普通反馈材料可以安全移入系统回收站，删除后仅使绑定轮次及相关批量任务失效。创建页保持原双栏和按钮外观，项目映射改为无竖线的四列结构化展示，并继续使用稳定路径保存材料绑定。

用户已完成本地验收，并授权在现有 main 分支提交、推送 GitHub，不另建分支。

## 修改前问题与根因

1. 工作流被“项目复盘”和“人工提出优化”模式切换拆成两页，用户无法同时看见项目、说明和材料，组合输入不直观，任务状态也缺少统一快照。
2. 项目复盘使用原生列表勾选状态，在 Windows 主题下可能显示黑色方块；单项目列表会被拉得过高。
3. 工作流任务只有任务文件和材料目录，没有完整输入快照，复制执行指令前无法重新验证项目、材料和公共工具是否变化。
4. 已保存反馈材料没有删除入口，错误材料只能手工处理；手工删除后任务和批量指令的失效反馈不明确。
5. 创建草稿删除 JSON 时，用户不容易确认源 JSON、源材料和其他项目绑定是否安全。
6. 截图脚本仍使用过期文案断言，导致首次材料 GUI 验收误报失败。
7. 创建页用竖线拼接映射字段，难以快速扫描；工作流项目列表缩短后仍保留原 stretch，且 QListWidgetItem 文本会与自定义行重复绘制。

## 视觉边界

本轮明确保留以下原有界面：

- 创建页双栏结构、顶部上移/下移/垃圾桶/编辑名称按钮和材料区按钮布局；仅把竖线文本改为四列对齐展示。
- 首页任务卡、反馈卡、按钮属性、间距和背景。
- 工作流页原页头、项目组控件、卡片背景、按钮属性和间距；两张内容页仅按用户要求合并为一个输入页。
- 批量反馈表格和自绘复选框的原有颜色、尺寸与绘制方式。
- 全局 QSS、卡片透明度、背景图和按钮样式。

允许的局部显示修复包括：创建页映射字段改为无竖线的结构化列；工作流两页合并且三类输入同屏；复用原批量反馈复选框以消除原生黑块；限制项目列表自身高度并启用内部滚动；在已保存反馈行增加与现有图标按钮一致的删除入口；修复“系统批量说明”状态文字重复。

## 工作流统一信息与服务模型

新增 WorkflowOptimizationInput：

- group_root
- selected_project_paths
- user_description
- material_paths

WorkflowOptimizationService 统一完成：

- 校验项目组与当前项目组的已完成项目目录。
- 对项目路径和材料路径稳定去重。
- 要求项目、说明、材料至少一项非空。
- 生成参考项目、项目记录、原始需求、反馈轮次、最新产品的只读快照。
- 生成材料文件名、大小、SHA-256 和复制后绝对路径清单。
- 写入当前优化任务和当前优化任务快照。
- 复制执行指令前重新验证任务、快照、公共工具、参考项目和材料。
- 新任务写入前，将上一轮任务、快照和材料一起原子归档。
- 写入失败时恢复上一轮；自动恢复不完整时保留旧数据恢复目录，不再由 finally 清理。
- 拒绝任务、快照、材料目录中的符号链接和非普通条目。

固定文件：

工作流优化/当前优化任务.md

工作流优化/当前优化任务快照.json

工作流优化/补充材料

工作流优化/历史优化任务/时间目录

## 页面状态联动

- 参考项目、优化说明和补充材料在同一页面共同维护。
- 项目、说明、材料任一存在即可生成。
- 输入发生变化后显示需要重新生成，并禁用复制执行指令。
- 生成期间禁用重复提交和输入控件。
- 当前任务存在但已过期时仍可查看，不能复制。
- 工作流项目使用公共 GlassCheckBox；批量反馈继续使用完全相同的绘制结果。
- 项目少时列表高度随内容收缩并紧贴标题区，项目多时只在列表内部滚动；底层 QListWidgetItem 不再重复绘制项目名称。

## 已保存反馈材料安全回收

新增 FeedbackService.recycle_saved_item，删除操作不由 UI 直接执行 Path.unlink。

回收前检查：

1. 项目目录存在且不是符号链接。
2. round_number 大于 0。
3. 客户反馈和第 N 轮目录真实存在，且没有越出当前项目。
4. 目标严格位于指定轮次根目录，是普通文件且不是符号链接。
5. 目标不是严格批次 ID 格式的系统批量说明。
6. expected_sha256 格式有效，并与删除前重新计算的 SHA-256 一致。
7. 文件复制到同轮次隐藏安全副本时复核设备、inode、大小和修改时间。
8. 回收前再次复核原路径、身份和安全副本哈希。
9. 使用 Send2Trash 移入系统回收站；失败时保留或恢复原文件。
10. 成功后保留轮次目录并重新扫描材料。

系统批量说明采用严格文件名规则“批量反馈说明-YYYYMMDD-HHMMSS-8位十六进制.txt”。相似的普通用户文件不会误判。系统说明显示明确标记和说明，不提供普通删除按钮。

## 删除后的任务失效

- 删除绑定轮次的普通材料后，TaskService 根据输入快照检测材料删除或变化，旧任务立即过期，复制执行指令禁用。
- 删除最后一个普通材料后，轮次目录保留，生成反馈修改任务的按钮禁用。
- 删除其他轮次材料不会错误影响当前任务。
- 批量保存材料被删除后，对应项目任务和批量执行指令都会失效；其他项目不受影响。
- 重新生成后，新快照只包含剩余有效材料。

## 创建项目草稿管理

创建页未改变双栏布局和按钮样式。映射表头和数据行改为四列结构化展示，删除全部竖线分隔；现有删除映射操作改为始终确认，并明确说明：

- 只从当前创建草稿移除映射。
- 不删除用户源 JSON。
- 已绑定材料只从该草稿解绑，不删除源材料。
- 删除后清理 json_files、project_names_by_path、materials_by_project 和当前选择。
- 使用规范化稳定路径键保存材料绑定，重排、改名、删除后不会把材料串到相邻 JSON。
- 自动选中相邻项目，并重新计算数量和创建按钮状态。
- “移除所选”和“清空材料”只作用于当前项目。

## 调研来源与采用结论

1. Qt QAbstractButton 官方文档
   https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QAbstractButton.html
   官方建议自定义按钮至少实现 paintEvent，并通常实现 sizeHint，必要时实现 hitButton。GlassCheckBox 继续复用项目原有的这套实现，没有改变颜色和尺寸。

2. Qt QAbstractScrollArea 官方文档
   https://doc.qt.io/qtforpython-6.10/PySide6/QtWidgets/QAbstractScrollArea.html
   文档明确滚动区域的可见内容绘制在 viewport 中，滚动条范围应随内容和 viewport 调整。本轮只约束工作流项目列表自身高度和内部滚动，没有改全局透明度或 QSS。

3. Qt QListWidget 官方文档
   https://doc.qt.io/qt-6/qlistwidget.html
   QListWidget 是项目型便捷列表；更复杂的大型动态列表可改用 QListView 和模型。本项目参考项目数量有限，继续使用现有 QListWidget，并只给工作流复选框使用轻量行控件，避免扩大布局改造范围。

4. Send2Trash 官方仓库
   https://github.com/arsenetar/send2trash/blob/master/README.rst
   Send2Trash 使用各平台原生回收站能力；Windows 使用 IFileOperation 或 SHFileOperation。实现中把它作为最终回收动作，并在调用前后增加身份、哈希、安全副本和失败恢复。

## 自动化与 GUI 验证

基线 HEAD：ce3543b16edbc4476677acb8552ac1e119bb67c6

基线复跑：139 passed，1 failed。失败项是组合框弹出视图尚未可见时立即执行真实鼠标点击，属于 GUI 时序断言不稳定；当前测试已等待窗口和 popup 可见后再点击。

当前结果：

- .venv/Scripts/python.exe -m pytest -q：162 passed。
- .venv/Scripts/python.exe app.py --smoke-test：通过。
- .venv/Scripts/python.exe -m pip check：无损坏依赖。
- .venv/Scripts/python.exe -m compileall -q app.py models services ui scripts tests：通过。
- scripts/capture_manual_workflow_screens.py：通过，生成 5 张截图。
- scripts/capture_initial_materials_screens.py：通过。
- scripts/capture_feedback_workflow_screens.py：通过，生成 13 张截图。
- scripts/capture_phase3_screens.py：通过；验证 3 个已完成项目在统一页面中可全选，并与优化说明、补充材料区域同时可见。

人工查看结果：创建页双栏、首页反馈、批量反馈的布局、背景、按钮和卡片风格与原界面一致；创建映射四列对齐且无竖线；工作流三类输入在一个页面内清楚呈现；已保存反馈行的打开和删除图标清晰对应同一行；批量反馈复选框外观没有变化。

## 主要修改文件

- services/workflow_optimization_service.py
- services/feedback_service.py
- services/task_service.py
- services/prompt_service.py
- ui/pages/workflow_optimization_page.py
- ui/pages/home_page.py
- ui/pages/create_project_page.py
- ui/widgets/glass_check_box.py
- ui/widgets/batch_feedback_panel.py
- ui/widgets/feedback_drop.py
- resources/prompt_templates/manual_workflow_optimization_task.md
- scripts/capture_manual_workflow_screens.py
- scripts/capture_initial_materials_screens.py
- tests/test_manual_workflow_optimization.py
- tests/test_feedback_recycle.py
- tests/test_batch_feedback.py
- tests/test_initial_materials.py
- tests/test_task_card_ui.py
- README.md

## 已知限制

- Send2Trash 是基于路径的系统调用。实现已经在调用前做多次身份与哈希复核并保留安全副本，但同一用户在极短时间窗口内并发替换文件仍属于操作系统级 TOCTOU 边界；当前桌面单用户场景风险较低。
- 工作流参考项目行继续使用 QListWidget.setItemWidget。当前数量和交互规模适合该方案；如果未来需要数百或数千项目，应改用 QListView、模型和 delegate，而不是继续增加行控件。
- 工作流页已按用户最新要求删除模式切换，但没有改动全局 QSS、背景、卡片透明度或按钮样式。
- tracked 验收截图已由脚本重新生成并随本轮有用改进一并提交；dist、deployment 和 Nuitka 崩溃报告不纳入版本控制。

## Git 状态

修改前 HEAD：ce3543b16edbc4476677acb8552ac1e119bb67c6

最终 commit SHA：以本次 main 分支提交为准。

远端推送：用户已授权，完成最终构建验证后推送 origin/main。
