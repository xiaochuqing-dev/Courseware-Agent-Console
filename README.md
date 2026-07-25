# 课件 Agent 控制台

Windows 桌面端规则自动化工具，当前版本 v1.0.0。已完成项目组创建、首次制作、多轮客户反馈、可选完整验收、手动归档、已完成项目查看及工作流优化 Prompt 闭环。GUI 只执行确定性文件规则，不接入任何模型 API。

标准项目目录只包含“原始需求”“客户反馈”“产品迭代”三个业务目录。旧版“工作文件 / 最终交付 / 验收记录”结构会在用户确认后先做同级完整备份，再安全迁移；不会静默覆盖用户文件。项目内标准文件夹被手动改名时，控制台会列出缺失目录和未知目录，由用户逐项指定对应关系后再修复。

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

构建 Windows standalone 版本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```
