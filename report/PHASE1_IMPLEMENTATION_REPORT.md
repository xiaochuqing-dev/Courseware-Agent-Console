# 阶段一实施报告

## 1. 阶段一目标

完成“创建项目组 → 导入并确认 JSON 映射 → 建立标准目录与公共资源 → 选择项目 → 生成首次制作任务 → 复制短执行提示词”的第一个可运行闭环。GUI 只执行确定性规则，不做需求理解、智能判断或模型调用。

## 2. 实际实现功能

- Windows 原生 PySide6 Widgets 桌面界面，首页和创建项目组页使用统一淡薄荷绿非均匀渐变背景。
- 创建项目组时配置目录名称、正整数项目数量、创建位置及同等数量 JSON。
- 显式显示“项目N → JSON 文件”映射，并支持上移、下移调整顺序。
- 校验空名称、Windows 非法名称、项目数量、JSON 数量、重复映射、文件扩展名、JSON 可解析性、目标目录和内置资源完整性。
- 在同级临时目录中完整构建项目组，成功后再改名；异常时清理本次临时目录，不覆盖已有目录。
- 为每个项目建立原始需求、客户反馈、产品迭代、当前任务.md 和项目记录.md。
- 原样复制 WORKFLOW.md、template.html、validate-tool.js 三个内置公共工具。
- 从独立资源模板复制根目录 AGENT任务规则.md。
- 首页从真实目录动态扫描项目N并按数字排序，不写死项目数量。
- 支持选择现有项目组、打开根目录、打开当前项目目录。
- 支持查看、编辑、保存及二次确认恢复默认任务规则。
- 支持特殊要求为空时生成或覆盖首次制作的当前任务.md。
- 支持复制简短执行入口，并显示轻量提示。
- 使用 QSettings 保存最近项目组；重启时自动恢复，路径失效时回到空状态。

## 3. 后续阶段功能

阶段一未实现客户反馈导入、反馈轮次、修改任务、验收提示词、完成归档、已完成项目查看、工作流优化、模型 API、智能调度、数据库、Web 后端、GitHub 推送和 CI/CD。这些均属于后续阶段。

## 4. 技术架构

- UI 层：MainWindow 作为壳，QStackedWidget 切换首页和创建页；可复用 Card、BackgroundWidget、Toast、规则编辑对话框。
- 服务层：ProjectService 负责校验、原子式目录创建、扫描和打开路径；TaskService 负责模板渲染和规则读写；SettingsService 封装 QSettings。
- 模型层：ProjectGroup 和 ProjectEntry 只描述磁盘扫描结果。
- 资源层：长期规则、当前任务、执行提示词及三个公共工具独立存放，不把长提示词散落在 Python 中。
- 持久化：业务数据完全使用本地目录和文本文件；QSettings 只保存最近路径。

## 5. 开源调研复核结论

- Qt for Python 官方文档确认 QMainWindow、QStackedWidget、QFileDialog、QSettings 和 pyside6-deploy 都是当前 Qt 6 的标准能力，适合本项目。
- PyOneDark 的侧边栏、页面容器和主题分离方式值得参考，但其完整模板结构和深色视觉不适合直接套用。
- PyQt-Fluent-Widgets 的导航与控件反馈可作为观感参考，但仓库使用 GPL-3.0；阶段一不引入该依赖，避免不必要的许可约束和主题框架耦合。
- 本项目最终采用原生 PySide6 Widgets + QSS + QPainter + 本地文件系统，依赖最少，许可边界清楚，且足以实现所需的 Windows 桌面体验。

## 6. 目录结构

```text
app.py
models/
services/
ui/
  pages/
  widgets/
  styles/
resources/
  prompt_templates/
scripts/
tests/
artifacts/
PHASE1_IMPLEMENTATION_REPORT.md
```

## 7. 核心交互流程

用户进入创建页填写项目组信息，选择与项目数量一致的 JSON，按显示顺序调整映射并创建。成功后自动返回首页并加载新项目组。用户选择项目、可选填写特殊要求，生成当前任务后即可复制短提示词，交由外部 Agent 按磁盘中的任务规则和公共工具执行。

## 8. Smoke Test 结果

- Python 模块编译通过。
- 应用无交互启动检查通过。
- 7 项 pytest 聚焦测试通过：三项目映射与目录结构、公共资源原样复制、数量不匹配拦截、已有目录不覆盖、打开路径准确、空特殊要求任务生成、QSettings 路径往返及窗口启动恢复。
- 自动化测试在系统临时目录生成公开的最小工具夹具，不随仓库分发真实 workflow、template 或 validate 文件。
- 首页和创建项目页均完成原生 Qt 渲染截图，中文字体、渐变背景、卡片层级、控件边界和映射展示正常。

## 9. 已知限制

- 当前只保存一个最近项目组，没有历史项目组列表。
- 项目创建后不支持在 GUI 内追加或重新映射 JSON；需要新建项目组或手动管理。
- 尚未生成安装包；开发环境直接通过 Python 启动，后续可使用 Qt 官方 pyside6-deploy。
- 公共工具在阶段一只能查看完整性，不能从 GUI 替换或编辑。

## 10. 下一阶段建议

优先加入客户反馈的文本、图片和文件导入，建立明确的第 N 轮目录规则，再基于模板生成反馈修改任务。归档与验收入口应等对应业务闭环完成后再展示。

## 11. 本地 Git 提交

提交信息：feat: complete phase 1 project creation and first-task workflow

仓库仅初始化并提交到本地，不配置远程，不推送 GitHub。

