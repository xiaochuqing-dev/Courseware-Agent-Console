# 课件 Agent 控制台

Windows 桌面端规则自动化工具，当前版本 v1.0.0。已完成项目组创建、首次制作、多轮客户反馈、产品验收、手动归档、已完成项目查看及工作流优化 Prompt 闭环。GUI 只执行确定性文件规则，不接入任何模型 API。

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
