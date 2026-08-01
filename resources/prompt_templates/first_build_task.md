# 当前任务

## 任务身份

- 项目显示名：{{PROJECT_NAME}}
- 项目 ID：{{PROJECT_ID}}
- 任务类型：首次制作
- 反馈轮次：0
- 版本号：{{VERSION_NUMBER}}
- 产物 ID：{{ARTIFACT_ID}}
- 预期输出：{{EXPECTED_OUTPUT}}
- 预期输出绝对路径：{{EXPECTED_OUTPUT_ABSOLUTE}}
- 生成时间：{{GENERATED_AT}}
- 输入快照 SHA-256：{{INPUT_SNAPSHOT_SHA256}}

## 本次特殊要求

{{SPECIAL_REQUIREMENTS}}

## 原始需求材料快照

{{ORIGINAL_MATERIALS}}

## 输入边界

- 只读取当前项目“原始需求”中的材料和项目组公共工具。
- 不读取任何客户反馈目录，不得假装处理反馈材料。
- JSON 是结构化主需求，其他图片、PDF、Word、PPT、表格、Markdown 和文本是补充内容或视觉参考。
- 无法读取的二进制材料必须逐项报告文件名、未读取范围和实际处理方式。

## 执行顺序

1. 读取并核对当前任务中的项目 ID、任务类型、反馈轮次 0 和输入快照。
2. 枚举“原始需求”全部文件，并与本任务的材料快照核对文件名和 SHA-256。
3. 读取当前环境能够读取的全部原始需求材料。
4. 读取项目组《AGENT任务规则.md》和绑定的 workflow。
5. 以绑定的 template 为唯一页面起点完成首次制作。
6. 保存到指定预期输出，不覆盖任何历史文件。
7. 写入正确的项目 ID、产物 ID、版本号 0 和反馈轮次 0 meta。
8. 真实运行绑定的 validate；error 必须全部修复，warning 必须修复或说明教学理由。
9. 追加更新《项目记录.md》，记录输入、输出、完成内容和验证结果。

## 完成条件

- 输出路径正确且未覆盖历史版本。
- 页面基于绑定 template 制作。
- validate 通过。
- 项目记录已更新。
- 无法读取的材料已如实报告。

{{BINDING_BLOCK}}
