# 课件 Agent 控制台

Windows 桌面端规则自动化工具。当前已完成项目组创建、首次制作、客户反馈轮次、反馈修改任务、完整产品验收 Prompt、手动归档和已完成项目查看闭环。GUI 只执行确定性文件规则，不接入任何模型 API。

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

阶段二页面与真实两轮反馈模拟：

```powershell
python scripts\capture_phase2_screens.py
```
