# 反馈工作流与任务上下文一致性重构实施报告

## 基本信息

- 完成日期：2026-08-01
- 目标分支：main
- 实施前 HEAD：55364b01767c8e4391b64e7e09b3684bd3f6e1b5
- 应用版本：1.1.0
- 最终 Git 提交：以本报告所在提交为准，推送后的完整 SHA 记录在最终交付信息中

## 修改前的真实问题

1. 任务类型由界面按钮状态间接决定。保存反馈后如果界面仍停留在首次制作，可能覆盖“当前任务.md”并生成缺少轮次和反馈材料的错误任务。
2. 单项目页只展示最新反馈轮次，旧轮次材料无法从界面回看；任务区域和反馈区域分别维护轮次状态，容易不同步。
3. 反馈任务只依赖模板文本，没有保存生成时的项目、材料、产品、要求、工具和批次身份，复制执行指令前也只检查文件是否存在。
4. 批量反馈嵌在当前项目页，页面过长且职责混合；轮次策略允许追加，和“每个项目独立创建下一轮”的业务规则冲突。
5. 表格复选框依赖原生主题，实际 Windows 主题下可能不可见；旧测试直接设置状态，不能证明鼠标点击有效。
6. 批量保存和任务生成跨多个项目写入，任一步失败时缺少完整事务回滚。
7. 界面固定显示 v1.0.0，无法判断 EXE 对应的代码构建。

## 根因分析

- 任务类型、反馈轮次和界面控件耦合，缺少明确的业务枚举和统一状态源。
- “当前任务.md”只有结果文件，没有与结果配套的输入快照和内容哈希，因此无法判断任务是否仍对应当前输入。
- 单项目和项目组功能没有按职责拆页，批量组件继承了单项目上下文和轮次策略。
- 自动化测试偏向调用内部方法，没有覆盖信号、鼠标命中区域、下拉选择和页面跳转。
- 多文件写入按步骤直接落盘，没有先准备、后提升、失败恢复的边界。

## 调研来源与采用结论

### Qt for Python：QStackedWidget

- 链接：https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QStackedWidget.html
- 可借鉴点：用稳定页面实例承载互斥功能，由外部导航切换当前页。
- 未直接照搬原因：项目已有 MainWindow、侧栏和页面栈，只需扩展现有壳层，不需要引入新的导航框架。
- 最终采用：把批量反馈做成独立 BatchFeedbackPage，注册到主窗口 QStackedWidget；返回单项目时恢复目标项目和轮次。

### Qt for Python：QTableWidget、QCheckBox 与样式表

- 链接：https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QTableWidget.html
- 链接：https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QCheckBox.html
- 链接：https://doc.qt.io/qt-6/stylesheet-reference.html#qcheckbox-widget
- 可借鉴点：表格可放置单元格控件；复选框应通过 checked/toggled 状态驱动业务；indicator 可定制。
- 未直接照搬原因：只依赖原生 indicator 仍会受 Windows 主题影响，样式表图片方案还会增加资源和缩放维护成本。
- 最终采用：HighContrastCheckBox 自绘边框、选中底色和白色勾号；整格鼠标点击复选框；状态变化统一更新项目提示输入框。

### Qt for Python：QComboBox 与 QSignalBlocker

- 链接：https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QComboBox.html
- 链接：https://doc.qt.io/qtforpython-6/PySide6/QtCore/QSignalBlocker.html
- 可借鉴点：使用 itemData 保存稳定轮次值；重建选项时临时屏蔽信号，避免刷新触发重复业务动作。
- 未直接照搬原因：控件本身不提供反馈历史语义，需要服务层轮次扫描和页面状态路由配合。
- 最终采用：反馈区只保留一个轮次下拉框，材料列表、追加按钮和任务生成都读取 currentData；刷新后尽量保持原选择，否则选择最新轮次。

### Qt Test：QTest.mouseClick

- 链接：https://doc.qt.io/qtforpython-6/PySide6/QtTest/QTest.html
- 可借鉴点：从用户输入路径验证鼠标命中、控件信号和状态变化。
- 未直接照搬原因：单次点击不足以证明完整工作流，还必须断言下游输入框、任务类型、材料列表和页面栈状态。
- 最终采用：新增复选框真实点击、下拉切换、保存按钮、页面入口和“查看项目”跳转测试，并检查视觉状态与内部状态一致。

### Spyder：SidebarDialog

- 链接：https://github.com/spyder-ide/spyder/blob/master/spyder/widgets/sidebardialog.py
- 可借鉴点：侧栏导航只承担功能选择，每个页面封装自己的内容和状态。
- 未直接照搬原因：Spyder 的插件和对话框基础设施远大于本项目，复制会引入无关层级。
- 最终采用：保留现有轻量主窗口结构，只新增独立批量反馈页和明确的页面切换信号。

### PyQt-Fluent-Widgets：Navigation 示例

- 链接：https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/master/examples/navigation
- 可借鉴点：导航入口与 QStackedWidget 页面一一对应，页面间通过显式路由切换。
- 未直接照搬原因：项目已有视觉体系；引入第三方主题框架会增加 GPL 许可、依赖和样式耦合。
- 最终采用：复用现有按钮、Card 和页面栈，实现同样的职责分离，不新增运行时依赖。

### Bazel：Remote Caching

- 链接：https://bazel.build/remote/caching
- 可借鉴点：结果能否复用应由完整输入集合及其内容摘要决定，而不是只看输出文件是否存在。
- 未直接照搬原因：本项目不是构建图系统，不需要 action graph、CAS 服务或远程缓存协议。
- 最终采用：为任务保存可读 JSON 快照和规范化 SHA-256；生成后立即校验，复制执行指令前按当前文件和配置重新构建输入摘要。

## 最终信息架构

### 当前项目页

- 当前任务：明确选择首次制作或反馈修改，显示任务类型、轮次和有效性状态。
- 单项目反馈：唯一轮次下拉框、对应材料列表、待保存材料、追加当前轮次、新建下一轮。
- 当前产品与验收：继续沿用现有职责。
- 批量反馈只保留侧栏入口，不再嵌入单项目反馈卡片。

### 批量反馈页

- 独立展示项目组级项目选择、当前轮次、目标轮次、本项目提示、统一材料和批量说明。
- 固定规则为每个选中项目独立创建自己的下一轮，不再提供轮次策略下拉框。
- 保存和任务生成完成后逐项目展示结果，并提供“查看项目”跳回对应项目和轮次。

## 任务类型路由

- 新增 TaskType.FIRST_BUILD 与 TaskType.FEEDBACK_MODIFICATION，服务层接口必须显式传入业务类型。
- 首次制作固定反馈轮次为 0，只读取原始需求、项目配置和公共工具，不包含反馈目录或批量字段。
- 反馈修改必须绑定明确轮次、该轮全部材料、特殊要求、最新有效产品和项目记录；没有有效产品或有效反馈材料时拒绝生成。
- 新建反馈轮次、追加已有轮次、选择历史轮次或从批量结果跳转后，界面自动进入反馈修改模式。
- 已有产品和反馈时切回首次制作，生成前显示确认警告，避免静默覆盖反馈任务。

## 反馈材料快照与任务失效

每次生成任务时同时写入“当前任务快照.json”，记录：

- 快照 schema、任务类型、反馈轮次、生成时间。
- 项目显示名、稳定项目 ID、项目根目录和项目配置摘要。
- 原始需求、当前轮次反馈材料、特殊要求、最新有效产品、目标输出和工具绑定。
- 每个材料和产品的文件名、类型、大小、实际路径和 SHA-256。
- 批量任务的批次 ID、批次记录路径、批量说明、本项目提示、项目边界及批次上下文摘要。
- “当前任务.md”、快照文件和输入集合的 SHA-256。

任务生成后立即做结构、语义和哈希校验。复制执行指令前重新扫描当前输入；材料、产品、特殊要求、项目配置、项目路径、工具绑定、目标输出或批次记录任一变化，任务状态变为“已过期”，仍可预览但不能复制执行。旧版本任务缺少快照时也拒绝复制，要求重新生成。

## 单项目反馈行为

- 下拉框列出全部已存在轮次，默认选中最新轮次。
- 切换轮次立即刷新对应材料列表，并绑定任务生成目标。
- “追加到第 N 轮”的 N 只取当前下拉选择；保存后保持该轮选中。
- “创建并保存为第 M 轮”的 M 始终是最新轮次加一，不受当前查看历史轮次影响。
- 重新处理已有反馈轮次时分配新的产品版本，避免覆盖历史产品。
- 材料或特殊要求变化后旧任务立即失效，按钮提示重新生成。

## 批量反馈与回滚边界

- 保存阶段先在临时 staging 目录准备批次记录和各项目材料，再提升到正式目录。
- 任一项目保存失败时回滚已提升材料和批次目录，避免半成功状态。
- 任务生成阶段先准备每个项目的任务、快照和批量任务，再统一替换；失败时恢复原任务、原快照和原项目配置。
- 批量执行指令会校验批次记录、批量任务、每个项目任务和快照的哈希，并重新调用单项目上下文校验。
- 项目最新轮次已变化、批次内容被改写、材料被改写或任务被改写时，批量执行指令拒绝复制。

## 构建身份

- APP_VERSION 更新为 1.1.0。
- 开发模式显示版本加当前 Git 短 SHA；无法读取 Git 时显示构建日期。
- Windows 构建脚本在打包前生成 resources/build_info.json，注入版本、完整 commit SHA 和构建日期；构建完成或失败后恢复原工作区状态。
- 运行时不访问网络，可从界面左侧版本文本确认 EXE 对应构建。

## 修改文件清单

服务与入口：

- app.py
- services/__init__.py
- services/build_info.py
- services/task_types.py
- services/task_service.py
- services/prompt_service.py
- services/batch_feedback_service.py

界面：

- ui/main_window.py
- ui/pages/__init__.py
- ui/pages/home_page.py
- ui/pages/batch_feedback_page.py
- ui/widgets/batch_feedback_panel.py
- ui/widgets/feedback_drop.py
- ui/styles/app.qss

任务模板与规则：

- resources/prompt_templates/first_build_task.md
- resources/prompt_templates/feedback_task.md
- resources/prompt_templates/AGENT任务规则.md

测试、构建与截图：

- tests/helpers.py
- tests/test_feedback_task_context.py
- tests/test_batch_feedback.py
- tests/test_batch_feedback_ui.py
- tests/test_task_card_ui.py
- tests/test_initial_materials.py
- tests/test_manual_workflow_optimization.py
- tests/test_naming_and_stable_identity.py
- tests/test_phase2_services.py
- tests/test_task_and_settings.py
- scripts/capture_feedback_workflow_screens.py
- scripts/capture_batch_feedback_screens.py
- scripts/build_windows.ps1

文档：

- README.md
- report/FEEDBACK_WORKFLOW_REFACTOR_REPORT.md

## 自动化测试结果

- 逐模块 pytest：14 个测试文件全部独立通过，合计 139 passed、1 skipped。
- 全量 pytest：单进程执行通过，139 passed、1 skipped，耗时 91.77 秒；此前观察到的 Windows PySide6 access violation 本轮未复现。
- 源码 smoke test：`python app.py --smoke-test` 通过。
- Python 编译检查：`python -m compileall -q app.py services ui scripts tests` 通过。
- 差异检查：`git diff --check` 通过；仅输出仓库现有 LF/CRLF 自动转换提示，无空白错误。
- GUI 截图脚本：Windows 原生 Qt 平台执行通过，13 张截图全部人工查看。
- Windows 正式构建：`scripts/build_windows.ps1` 通过，生成 CoursewareAgentConsole.exe；构建目录内 build_info.json 包含 v1.1.0、commit SHA 和 2026-08-01，源码目录临时注入文件已恢复删除。
- 构建后 EXE smoke test：通过。
- 构建警告：pyside6-deploy 未找到额外项目文件和 dumpbin，均为非阻断警告；EXE 产出、资源注入和启动检查正常。

## GUI 人工验收与截图

使用 Windows 原生 Qt 平台运行 scripts/capture_feedback_workflow_screens.py，生成 13 张 1280×900 截图；同时脚本检查 860×560、1100×720 和 1280×900 三档窗口，当前项目页横向滚动最大值均为 0。

截图覆盖：无反馈空状态、两轮轮次选择、第1轮与第2轮不同材料、待保存材料和按钮联动、新建第3轮后自动进入反馈修改、独立批量页、多项目真实点击、选中/未选中视觉差异、独立目标轮次、保存结果、任务可复制状态及批量结果跳回单项目。

本机输出目录：artifacts/feedback_workflow_refactor。截图属于验收产物，不纳入 Git 提交。

## 已知限制

- 旧任务没有 schema 2 快照时不能继续复制执行，这是为防止错误复用而设置的兼容边界。
- 快照能验证已绑定文件、配置和批次内容，但不能替代 Agent 执行后的真实业务验收。
- Windows PySide6 曾在旧验收中出现单进程跨模块 access violation；本轮逐模块和全量测试均通过，仍保留逐模块结果作为可定位的回归证据。
- 离屏 Qt 平台在当前 Windows 环境不加载系统字体，因此正式截图必须使用 Windows 原生 Qt 平台；离屏模式只用于无字体要求的自动化断言。
