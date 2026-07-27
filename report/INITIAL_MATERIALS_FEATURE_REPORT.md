# 首次制作材料功能报告

## 1. 当前问题

原创建流程只支持“一项目一 JSON”。图片、PDF、Office 文档和其他首次制作参考材料无法绑定到具体项目，也不会复制到对应“原始需求”或写入项目配置。

## 2. 开源项目调研

调研基于 2026-07-27 拉取的固定提交：

- Spyder，提交 17f0cae5fca1b818b414e99837c0cabe7636a5ab
  - 多文件选择与上传前存在性检查：https://github.com/spyder-ide/spyder/blob/17f0cae5fca1b818b414e99837c0cabe7636a5ab/spyder/plugins/explorer/widgets/remote_explorer.py
  - 项目文件保序去重、相对路径保存和失效路径过滤：https://github.com/spyder-ide/spyder/blob/17f0cae5fca1b818b414e99837c0cabe7636a5ab/spyder/plugins/projects/api.py
  - 对应保序去重测试：https://github.com/spyder-ide/spyder/blob/17f0cae5fca1b818b414e99837c0cabe7636a5ab/spyder/plugins/projects/widgets/tests/test_project.py
- napari，提交 0f3ca3deadc2d92998a2676d4c228d64ce2b3f61
  - PySide6/PyQt 兼容的 getOpenFileNames、多文件整体传递、取消无副作用和后台打开入口：https://github.com/napari/napari/blob/0f3ca3deadc2d92998a2676d4c228d64ce2b3f61/src/napari/_qt/qt_viewer.py
  - 文件选择回归测试：https://github.com/napari/napari/blob/0f3ca3deadc2d92998a2676d4c228d64ce2b3f61/src/napari/_qt/_tests/test_open_file.py
- Orange3，提交 86f208ce4434ce4f6925ecef397f2ccc1d0b3657
  - RecentPath 稳定路径对象、已存在项移到列表头部而不重复：https://github.com/biolab/orange3/blob/86f208ce4434ce4f6925ecef397f2ccc1d0b3657/Orange/widgets/utils/filedialogs.py
  - 文件选择取消立即返回、路径记录与读取器元数据绑定：https://github.com/biolab/orange3/blob/86f208ce4434ce4f6925ecef397f2ccc1d0b3657/Orange/widgets/data/owfile.py

参考点是多选后再处理、保序去重、稳定路径身份、取消无副作用、写入前验证和测试覆盖。没有直接照搬，因为本项目已有 PySide6 页面、JSON 映射、项目级后台线程、staging 原子创建和中文配置约定；直接引入其他项目的模型或插件层会增加无关依赖并破坏现有结构。

## 3. 最终交互方案

创建页区域改为“项目映射与首次材料”。映射行显示顺序、项目名、材料数量和 JSON 文件。选择映射后显示当前项目、当前 JSON、材料文件名、可读大小和完整路径 Tooltip，并提供“添加图片/材料”“移除所选”“清空材料”。

添加使用多文件选择器，包含图片、文档、文本和所有文件过滤器。取消选择不改变现有列表。删除带材料的 JSON 映射必须确认，并明确显示材料数量。创建期间 JSON、项目名、材料和工具控件全部锁定。

## 4. 数据结构设计

界面状态：

```python
materials_by_project: dict[str, list[Path]]
```

键为规范化 JSON 物理路径，不使用列表序号。后端接口使用同一结构：

```python
project_materials: dict[str, list[Path]]
```

项目配置新增兼容字段：

```json
{
  "source_materials": [
    {
      "source_id": "UUID",
      "file_name": "教材截图.png",
      "sha256": "SHA-256",
      "size": 123456
    }
  ]
}
```

项目组索引仅记录 source_material_count 统计，不记录用户源文件绝对路径。

## 5. 材料与项目的稳定绑定

JSON 规范化路径同时作为项目名称和材料列表的稳定键。上移、下移只改变 json_files 顺序；改名只更新 project_names_by_path；二者均不改变材料键。同一物理文件允许主动绑定到不同项目，同一项目内重复物理路径按首次出现顺序保留一次。

## 6. 文件复制和冲突处理

创建前一次性完成全部材料验证：

1. 路径存在。
2. 是普通文件。
3. 可打开读取并可计算 SHA-256。
4. 同一项目内物理路径去重。
5. 两个不同源文件同名时阻止创建。
6. 材料与项目 JSON 同名时阻止创建。
7. 未绑定材料的项目正常通过。

验证全部通过后才创建 staging。每个项目内部依次复制 JSON 和材料；每个目标写入前检查不存在，复制后核对大小和 SHA-256。任一失败会清理 staging，不留下目标目录或半成品。用户源文件只读并通过 shutil.copy2 复制。

## 7. 项目配置兼容方案

项目配置 schema_version 保持 1，因为 source_materials 是可选、向后兼容的附加字段。新项目总是写入数组，无材料时写空数组。读取旧项目时缺失字段会在内存中补为空数组，不要求整体迁移，也不修改旧配置文件。旧项目迁移路径创建的新配置同样写入空数组。

## 8. 修改文件清单

- ui/pages/create_project_page.py
- services/project_service.py
- services/__init__.py
- resources/prompt_templates/first_build_task.md
- resources/prompt_templates/AGENT任务规则.md
- tests/test_initial_materials.py
- scripts/capture_initial_materials_screens.py
- README.md
- report/INITIAL_MATERIALS_FEATURE_REPORT.md

## 9. 自动化测试结果

- 同步前基线：73 passed。
- 功能完成后：88 passed in 40.79s。
- 覆盖无材料、多图片与 PDF、多项目隔离、部分项目无材料、重排、改名、删除确认、物理路径去重、两类同名冲突、缺失/目录/不可读文件、复制内容、大小、SHA-256、旧配置、Prompt、连续取消选择和 8 MiB 材料后台复制时的 GUI 响应。
- python app.py --smoke-test：退出码 0。
- git diff --check：通过，仅有仓库现有 Windows CRLF 提示。

## 10. 真实 GUI 回归结果

scripts/capture_initial_materials_screens.py 创建了 3 个真实项目：

- 项目 1：两张图片。
- 项目 2：一张图片和一个 PDF。
- 项目 3：无材料。

验收中完成 JSON 重排和项目改名，再通过真实后台线程创建。逐项目核对了目录、JSON、材料数量、源文件字节、source_materials、首次任务内容和 GUI 事件循环响应，全部通过。

## 11. 截图路径

- artifacts/initial-materials-create-empty.png
- artifacts/initial-materials-project-with-multiple.png
- artifacts/initial-materials-multi-project-counts.png
- artifacts/initial-materials-conflict-warning.png
- artifacts/initial-materials-created-home.png

## 12. 已知限制

- GUI 只复制材料，不解析、识别、预览或智能分类。
- Windows 目标文件名按不区分大小写处理冲突。
- 材料在验证后被外部修改时，复制校验会失败并回滚整个 staging。
- 旧项目缺少 source_materials 时按空数组读取，不反向扫描旧“原始需求”自动补录历史材料。
- 超大文件不会阻塞 GUI 主线程，但会按文件大小增加后台创建时间。

## 13. 提交信息

- 开始实现基线：288c086e1b89f927c944be0add35d1ef6904ad38
- 功能最终提交：cf8e0dd8e3f9275a57ce4bf6ee77fe89db8a2d75
- 目标分支：main
- 目标提交信息：feat: add per-project initial materials
