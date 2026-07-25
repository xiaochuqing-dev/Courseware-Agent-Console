# 项目可读命名与稳定 ID 改进报告

## 基线与提交

- 基线分支：最新 `main`
- Base commit：`ee50581b7291d00c626fb64034cbadd438989268`
- 最终功能 commit：`f4fc22cec4dc3e15ff7a7d269915638ee791c4e8`

## 问题与目标

旧版以“项目1～项目N”和“初始版本/第N轮修改”作为主要可见名称。多个课件并行时，用户必须打开原始需求才能确认内容；目录或 HTML 被手工改名后，基于名称的识别也容易失效。本次同时解决可读命名和稳定身份，避免只改界面文字而底层仍依赖名称。

## 新命名规则

- 新项目默认使用原始 JSON 文件名的 stem 作为完整 `display_name`，创建前允许编辑。
- 物理目录使用统一的 Windows 安全名称；处理非法字符、控制字符、结尾空格/句点、保留名、重名和长路径。
- 名称过长时仅截短 `directory_name`，保留完整 `display_name`，并附稳定短哈希避免冲突。
- 重名目录依次使用“名称”“名称（2）”“名称（3）”。
- 首次产品为“课题名.html”，第 N 轮产品为“课题名（N）.html”，不再使用旧版通用名称。
- `order` 只负责排序，不再作为项目身份或主要可见名称。

## 稳定身份与存储

每个项目使用稳定 `project_id`，每次预期生成的产品使用新的 `artifact_id`。项目根目录的 `项目配置.json` 保存项目身份、显示名、实际目录名、来源 JSON、产品基础名、已知目录名和 artifacts；项目组配置升级到 schema v3，保存 `group_id` 及各项目的 `project_id`、`order`、`display_name`、`directory_name`。最后选择保存 `group_id + project_id`，归档、已完成项目和工作流页面也以 ID 定位。

本次不使用数据库：数据规模小，项目需要保持可复制、可归档、可人工检查的文件夹形态；项目内 JSON 可与项目一起移动和备份，也避免新增服务、部署依赖及数据库与文件系统之间的事务一致性问题。

## HTML 身份与识别

任务模板和 Agent 规则要求产品 HTML 在 `<head>` 写入以下不可见 meta：

```html
<meta name="courseware-project-id" content="PROJECT_UUID">
<meta name="courseware-artifact-id" content="ARTIFACT_UUID">
<meta name="courseware-version" content="N">
<meta name="courseware-feedback-round" content="N">
```

最新产品识别不依赖修改时间，优先级为：

1. 项目配置 artifacts 中最大的 `version_number`
2. HTML meta 中的 `artifact_id`
3. 已登记 SHA-256
4. `current_name` 或 `aliases`
5. 新规范文件名
6. 旧文件名迁移兼容
7. 无法唯一识别时由用户手工绑定

文件内容变化但 ID 保留时继续识别、更新哈希并使旧验收状态失效；meta 丢失时依次回退到哈希、别名和规范名。

## 重命名体验

- 项目目录改名后通过目录内 `project_id` 找回，自动纠正实际路径，再由非阻断横幅让用户选择是否采纳新显示名。
- HTML 改名后通过 `artifact_id` 继续识别，横幅提供“以后按新名称识别”“恢复规范名称”“忽略”。采纳后更新 `current_name` 和 `aliases`，不改变版本号与 ID，后续不重复提示。
- 未登记 HTML 使用非阻断绑定入口；普通重命名、可恢复异常和迁移提示不会阻塞首页，也不会在启动时连续弹出模态窗口。

## 旧数据迁移

- 旧“项目1～项目N”通过临时事务目录迁移；成功或正常失败后自动清理，不创建持久备份。
- 从每个项目唯一原始 JSON 推导课题名，补齐 `project_id` 和项目配置，升级项目组 schema v3，并安全重命名目录。
- 原始需求、客户反馈、产品迭代、当前任务和项目记录完整保留；已有稳定 ID 在结构迁移时保留。
- 旧“初始版本/第N轮修改”产品迁移为新命名并建立 artifact 索引；冲突不覆盖，失败时原目录保持不变。

## 验证结果

- 全量测试：`pytest`，73 项全部通过。
- 源码烟测：`python app.py --smoke-test`，通过。
- Windows EXE 烟测：退出码 0；实际启动后进程正常保持运行。
- EXE：`dist/CoursewareAgentConsole.dist/CoursewareAgentConsole.exe`
- EXE SHA-256：`81ECC68ECF16BB341FD84D87C55B85B52F0A275AC6D83ED92D9549BB46D5F250`
- 构建资源与源码资源哈希一致。

## 截图

- `artifacts/naming-create-projects.png`：六个 JSON 的创建映射和最终目录名
- `artifacts/naming-project-list.png`：左侧真实课题名列表
- `artifacts/naming-first-product.png`：首次产品命名
- `artifacts/naming-feedback-products.png`：第 N 轮产品命名及最新版本识别
- `artifacts/naming-rename-notice.png`：目录与 HTML 改名的非阻断提示
- `artifacts/naming-migration-preview.png`：旧项目迁移预览

本次未引入数据库、后端、Tag 或 Release，也未改变已有创建后台线程、防重复点击、静默子进程、安全删除和单实例机制。
