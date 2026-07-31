# 多课件批量反馈与 Word 支持实施报告

完成日期：2026-07-31

## 修改前问题

反馈导入只绑定左侧单个项目，不支持 Word，也无法把一份客户材料安全地同时写入多个课件。不同项目的反馈轮次不能共用一个数字，原有逐文件保存和逐项目任务生成也不具备整批回滚语义。

## 最终产品交互

首页“客户反馈导入”新增“当前项目反馈 / 批量反馈”模式切换，默认保留当前项目反馈。批量页依次提供目标课件多选、轮次策略、独立目标预览、每课件提示、统一材料、批量补充说明、保存、任务生成、复制批量执行指令和完整结果区。批量与单项目状态互不污染，切换左侧项目不清空批量状态；带未保存批量内容切换项目组时必须确认。

## 独立轮次设计

目标项目始终用稳定 project_id 识别。默认策略按项目分别计算“最新轮次 + 1”，无历史项目为第 1 轮；追加策略按项目追加到各自最新轮次，任一项目无历史时整体禁用。提交前重新加载项目组并扫描全部轮次，项目路径、最新轮次、目标轮次或策略与预览不一致时停止提交并刷新预览。

## 批量事务和回滚边界

保存前统一检查项目身份与结构、材料存在性和可读性、格式和大小、同名冲突、目标冲突、写权限及 Windows 安全路径长度。全部材料先进入项目组“批量反馈”下的隐藏临时目录，复制后核对大小和 SHA-256，再统一提交。新建轮次失败时删除本批新轮次；追加失败时只删除本批新增文件，保留原有材料。批次记录只在所有项目提交成功后出现。

批量任务先为所有项目准备并校验独立任务和项目配置，再统一替换。替换前保存每个“当前任务.md”和“项目配置.json”的字节快照；任一替换、批量任务文件或记录更新失败时全部恢复，不保留部分项目的新任务。

## Word 支持范围

支持选择、拖拽、待保存显示、保存和重新扫描 DOCX 与 DOC。DOCX 只校验 ZIP 容器及 [Content_Types].xml、word/document.xml 关键部件；DOC 仅作为旧版二进制原始材料保存。两者均只复制，不移动、不修改源文件，不依赖 Microsoft Word。DOCM 明确拒绝，不承诺完整解析图片、文本框、批注、修订、页眉页脚和嵌入对象。

## 防串项目设计

批量补充说明描述整份材料结构，每课件提示按 project_id 保存。每个目标轮次生成“批量反馈说明-批次ID.txt”，写明当前项目名称、稳定 ID、真实目标轮次、全部目标课件、项目提示和明确禁止串项目的边界。反馈任务同时要求优先读取原始 Word，无法读取时如实报告；内容无法归属时不得猜测。

## 批次目录和记录

目录为“项目组/批量反馈/批次-YYYYMMDD-HHMMSS-唯一后缀”。批量反馈记录.json 保存批次与项目组 ID、时间、策略、材料源路径/大小/SHA-256、批量说明，以及每个项目的 ID、显示名、路径、提交前轮次、目标轮次、提示、保存路径、反馈状态、任务路径和任务 SHA-256。

## 批量任务与执行指令

每个项目仍生成自己的“项目目录/当前任务.md”，使用各自反馈轮次、产品版本、产物 ID、预期输出和真实工具绑定。批次目录另生成“批量反馈任务.md”，只列执行顺序、各项目真实绝对任务路径和隔离边界。

复制内容为：

```text
请读取并完整执行以下批量任务文件：

<项目组绝对路径>\批量反馈\批次-...\批量反馈任务.md
```

复制前重新核对批量任务文件哈希，以及每个项目任务的文件哈希、项目 ID、批次 ID 和反馈轮次；任一任务被删除或改写都会拒绝复制。

## 重要边界结果

同一物理文件保序去重；不同来源同名、目标已有同名、源文件失效、符号链接、项目移动/删除/归档、结构损坏、路径过长和预览过期均整批阻止。保存和任务生成期间禁用可变控件并由服务锁拒绝重复提交。追加模式回滚不删除历史材料。隐藏临时目录不符合“第N轮”规则，不会被重新扫描为正式反馈轮次。

## 私人资源清理

仓库不再保存或打包默认 workflow、template、validate 实体文件，也不保留本机构建日志。测试和截图脚本在系统临时目录动态生成公开的最小工具夹具；旧报告中的私人工具绝对路径和哈希已脱敏。正式创建项目组仍要求用户显式选择自己的真实工具。

## 修改文件

核心新增 services/batch_feedback_service.py、ui/widgets/batch_feedback_panel.py、tests/test_batch_feedback.py、tests/test_batch_feedback_ui.py、scripts/capture_batch_feedback_screens.py。同步修改 FeedbackService、TaskService、HomePage、MainWindow、反馈 Prompt、样式、服务导出、测试夹具、历史截图脚本、README 和资源清单。

## 自动化测试

最终全量测试按 13 个模块分别在独立 pytest 进程中执行：128 项收集，127 passed，1 skipped。跳过项仅为当前 Windows 权限不允许创建测试符号链接；服务本身已实现符号链接拒绝。Windows 下将全部 GUI 模块放在同一长生命周期 PySide6 进程时偶发原生 access violation，模块隔离后所有业务断言均通过。Smoke test、Python 编译检查和 git diff --check 通过。

## Windows 打包与快捷方式

scripts/build_windows.ps1 已完成一次清理后的正式构建，打包版 smoke test 退出码为 0。构建脚本会先清理旧 dist/deployment，结束时强制检查资源包不得包含已移除的 resources/default_public_tools；最终包内也未发现 WORKFLOW.md、template.html、validate-tool.js 或 validate.js 实体文件。桌面“课件Agent控制台”快捷方式已刷新，并核对目标为本次生成的 dist/CoursewareAgentConsole.dist/CoursewareAgentConsole.exe。

## GUI 验收

真实 Qt GUI 脚本 scripts/capture_batch_feedback_screens.py 通过，生成 8 张截图：

- artifacts/batch-feedback-empty.png
- artifacts/batch-feedback-different-rounds-preview.png
- artifacts/batch-feedback-missing-round-preview.png
- artifacts/batch-feedback-word-image-hints.png
- artifacts/batch-feedback-save-success.png
- artifacts/batch-feedback-tasks-and-copy.png
- artifacts/batch-feedback-same-name-conflict.png
- artifacts/batch-feedback-single-mode-regression.png

脚本同时断言 860x560、1100x720、1440x900 下无横向滚动，连续模式/项目切换状态稳定，剪贴板指令与真实批量任务路径一致。

## 已知限制

控制台不理解或拆分 Word 语义，DOC 不做正文解析。跨多个目录的提交可在捕获到的异常中完整回滚，但操作系统断电或进程被强制终止不具备文件系统级分布式事务保证；残留隐藏暂存目录不会被识别为反馈轮次。任务生成要求本批目标轮次仍是各项目最新轮次，后续出现新轮次时需开始新批次。

## Git 提交信息

目标分支：main。提交标题：feat: add atomic batch feedback and Word support。最终 SHA 与推送结果以本报告所在提交和最终交付汇报为准。
