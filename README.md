# 课件 Agent 控制台

Windows 桌面端规则自动化工具，当前版本 v1.0.0。已完成项目组创建、首次制作、多轮客户反馈、可选完整验收、手动归档、已完成项目查看及工作流优化 Prompt 闭环。GUI 只执行确定性文件规则，不接入任何模型 API。

标准项目目录只包含“原始需求”“客户反馈”“产品迭代”三个业务目录。旧版“工作文件 / 最终交付 / 验收记录”结构会按原样打开，不自动备份或迁移。项目内文件夹被改名或删除时，控制台只在异常首次出现或发生变化时显示一次短暂提示；同一异常在重启后也不会重复，恢复正常后再次异常才会重新提示。提示位于客户反馈区顶部，不弹出阻塞窗口，也不自动修改用户文件。

创建项目组时，每个 JSON 映射都可以独立绑定零个或多个首次制作材料。支持图片、PDF、Office 文档、文本和任意其他文件；同一文件可主动绑定到不同项目，项目内按规范化物理路径保序去重。材料只复制到对应项目的“原始需求”，不会移动或修改源文件。创建前会统一检查文件存在性、普通文件类型、可读性和同名冲突；复制后会核对大小与 SHA-256。项目配置的 source_materials 只记录文件名、大小、哈希和稳定 ID，不保存用户源文件绝对路径；旧配置缺少该字段时按空数组兼容。

创建和工具预验证在后台线程运行，界面显示真实阶段并阻止重复提交。Windows 子进程静默运行。项目组本地删除使用系统回收站，成功后才清理控制台记录。正式启动为单实例，重复启动会唤醒已有窗口。

## 运行环境

- Python 3.11+
- PySide6 6.x

安装并启动：

```powershell
python -m pip install -r requirements.txt
python app.py
```

运行核心检查：

```powershell
python -m pytest -q
python app.py --smoke-test
```

阶段三完整回归与截图：

```powershell
python scripts\capture_phase3_screens.py
```

首次材料功能 GUI 验收与截图：

```powershell
python scripts\capture_initial_materials_screens.py
```

截图写入 artifacts，覆盖无材料页面、多项目不同材料数量、单项目多材料、冲突提示和创建完成首页。首次制作任务会要求 Agent 先枚举“原始需求”中的全部文件，JSON 作为结构化主需求，其他文件作为补充内容和视觉参考；无法读取的二进制材料必须如实记录。

构建 Windows standalone 版本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```
