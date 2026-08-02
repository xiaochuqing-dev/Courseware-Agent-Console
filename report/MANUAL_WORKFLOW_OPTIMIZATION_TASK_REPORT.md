# 人工工作流优化任务与复制执行指令实施报告

## 修改前的问题

工作流优化页只能从已完成项目生成复盘 Prompt，不能把用户临时发现的问题和补充材料保存成项目组级固定任务。首次制作和反馈修改虽然会生成“当前任务.md”，但用户仍需自行组织 Agent 执行文字。现有工作流实施按钮复制长 Prompt，不适合作为固定任务文件的短入口。

## 最终交互方案

工作流优化页将参考项目、优化说明和补充材料合并到一个页面。三类输入均可选，任一非空即可生成，也可任意组合；材料列表支持保序去重、移除所选和清空。生成在后台线程执行，期间禁用重复提交。成功后显示任务状态并启用“复制执行指令”。

首页首次制作和反馈修改各有独立的“复制执行指令”按钮。按钮仅在当前模式已有匹配任务时启用。当前任务预览弹窗的复制按钮也统一使用该名称。

## 目录和文件设计

```text
项目组/
├─ AGENT任务规则.md
├─ 公共工具/
│  ├─ WORKFLOW.md
│  ├─ template.html
│  └─ validate-tool.js
└─ 工作流优化/
   ├─ 当前优化任务.md
   ├─ 补充材料/
   └─ 历史优化任务/
      └─ YYYYMMDD-HHMMSS-ffffff/
         ├─ 当前优化任务.md
         └─ 补充材料/
```

历史目录名只使用数字和连字符，兼容 Windows，并通过微秒和递增后缀避免冲突。旧项目组缺少“工作流优化”目录时自动创建，不修改任何课件项目内部结构。

## 任务模板设计

模板文件为“resources/prompt_templates/manual_workflow_optimization_task.md”，固定包含：

1. 用户说明：原样保存用户输入，不擅自扩展或弱化。
2. 补充材料：写入绝对材料目录和本轮材料清单；无材料时明确说明直接按用户说明执行。
3. 需要检查的公共工具：只列出 WORKFLOW.md、template.html 和 validate-tool.js 的真实绝对路径。
4. 执行要求：限定相关修改、兼容性、测试、说明更新和结果记录。

模板不默认要求修改 AGENT任务规则.md，也不要求三个公共工具每次全部修改。

## 材料处理与归档规则

选择相同物理文件时按首次选择顺序去重。不同源文件同名时生成前阻止；与“当前优化任务.md”同名的材料也会阻止。生成前验证文件存在、不是目录或符号链接并且可完整读取。

新材料先复制到隐藏临时目录，复制后核对大小与 SHA-256。全部准备完成后才切换当前任务。上一轮任务和材料先移动到临时归档，任何复制、切换或归档失败都会撤回新文件并恢复旧任务。源文件始终只读复制，不移动、改写或删除。

所有控制台生成路径都由当前项目组下的固定目录名构造；工作流优化目录和管理子目录如果是符号链接或错误文件类型会被拒绝。

## 复制执行指令设计

三个使用位置统一复制：

```text
请读取并完整执行以下任务文件：
<真实绝对任务路径>
```

首页首次制作和反馈修改指向当前课件项目的“当前任务.md”；工作流优化指向项目组的“工作流优化/当前优化任务.md”。复制操作不生成任务、不修改任务内容。文件不存在或当前模式任务不匹配时不复制错误路径。

## 修改文件清单

- services/workflow_optimization_service.py
- services/prompt_service.py
- services/__init__.py
- resources/prompt_templates/manual_workflow_optimization_task.md
- ui/pages/workflow_optimization_page.py
- ui/pages/home_page.py
- tests/test_manual_workflow_optimization.py
- tests/test_task_card_ui.py
- scripts/capture_manual_workflow_screens.py
- README.md
- artifacts/manual-workflow-empty-materials.png
- artifacts/manual-workflow-multiple-materials.png
- artifacts/manual-workflow-generated.png
- artifacts/home-first-build-execution-instruction.png
- artifacts/home-feedback-execution-instruction.png

## 自动化测试结果

- 全量 pytest：162 passed。
- smoke test、pip check、compileall：通过。
- 四套 GUI 验收脚本：通过。
- git diff --check：通过。

新增测试覆盖无材料、多类型材料、物理文件去重、不同源文件同名、管理文件同名、文件缺失、目录冒充、不可读取、完整历史归档、复制失败回滚、归档失败回滚、中文与空格绝对路径、文件不存在、取消选择、大文件后台复制、首页两种任务模式复制以及原项目复盘回归。

## 真实 GUI 验收结果

“scripts/capture_manual_workflow_screens.py”已实际运行通过，剪贴板内容逐项校验，并生成：

- artifacts/manual-workflow-empty-materials.png
- artifacts/manual-workflow-multiple-materials.png
- artifacts/manual-workflow-generated.png
- artifacts/home-first-build-execution-instruction.png
- artifacts/home-feedback-execution-instruction.png

统一工作流页已检查 860×560、1100×720 和 1366×860，无横向溢出；参考项目、空材料、多材料和成功结果页面无重叠。首页两种模式的按钮、任务状态和操作区均完整可见。

## 已知限制

控制台只负责确定性文件操作，不解析材料内容，也不接入模型 API。Agent 能否读取 Office 或其他二进制材料取决于实际执行环境；任务模板要求无法读取时如实说明。历史任务由时间目录保存，界面当前不提供历史浏览和恢复操作，可直接通过文件管理器访问。

## 最终提交信息

目标分支为 main，推送目标为 origin/main。提交标题使用“feat: add manual workflow optimization tasks”，提交 SHA 和最终推送结果以 Git 完成后的交付汇报为准。
