# 课件 Agent 控制台

Windows 桌面端规则自动化工具。阶段一提供项目组创建、JSON 映射、公共工具复制、任务规则编辑、首次任务生成、短提示词复制和项目目录打开能力，不接入任何模型 API。

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

