# 当前任务

## 任务身份

- 项目显示名：{{PROJECT_NAME}}
- 项目 ID：{{PROJECT_ID}}
- 任务类型：反馈修改
- 反馈轮次：{{FEEDBACK_ROUND}}
- 反馈目录：{{FEEDBACK_DIRECTORY}}
- 版本号：{{VERSION_NUMBER}}
- 产物 ID：{{ARTIFACT_ID}}
- 预期输出：{{EXPECTED_OUTPUT}}
- 预期输出绝对路径：{{EXPECTED_OUTPUT_ABSOLUTE}}
- 生成时间：{{GENERATED_AT}}
- 输入快照 SHA-256：{{INPUT_SNAPSHOT_SHA256}}

## 本轮特殊要求

{{SPECIAL_REQUIREMENTS}}

## 本轮反馈材料

{{FEEDBACK_MATERIALS}}

{{BATCH_CONTEXT}}

## 修改基线

- 当前登记的最新有效产品：{{BASELINE_PRODUCT_NAME}}
- 最新有效产品路径：{{BASELINE_PRODUCT_PATH}}
- 产品版本：{{BASELINE_PRODUCT_VERSION}}
- 产品产物 ID：{{BASELINE_PRODUCT_ARTIFACT_ID}}
- 产品文件 SHA-256：{{BASELINE_PRODUCT_SHA256}}
- 项目记录路径：{{PROJECT_RECORD_PATH}}
- 原始需求路径：{{ORIGINAL_REQUIREMENTS_PATH}}

必须基于上述最新有效产品做最小必要修改，不得从旧版本或 template 重新开始。

## 输入优先级

1. 当前任务特殊要求。
2. 当前{{FEEDBACK_ROUND}}最新反馈材料。
3. 此前已经确认和完成的修改要求。
4. 原始需求。
5. WORKFLOW。
6. template。

## 执行顺序

1. 读取当前任务并核对任务类型、项目 ID、反馈轮次、产物 ID 和输入快照。
2. 枚举“客户反馈/{{FEEDBACK_ROUND}}/”全部文件。
3. 对照任务中的本轮材料清单，核对文件名、大小、实际路径和 SHA-256。
4. 读取当前环境能够读取的全部反馈材料；原始 Word、PDF、图片和文本均不得遗漏。
5. 无法读取的二进制材料必须报告具体文件、限制和未读取范围，不得假装已经读取。
6. 读取项目记录、原始需求和上述最新有效产品。
7. 按本轮反馈、特殊要求和输入优先级做最小必要修改。
8. 不得从旧版本重新开始，不得读取或处理其他项目。
9. 保存到指定新版本输出文件，不覆盖任何历史版本。
10. 写入正确的项目 ID、产物 ID、版本号和反馈轮次 meta。
11. 真实运行绑定的 validate；error 必须全部修复，warning 必须修复或说明教学理由。
12. 追加更新项目记录，写明本轮材料、修改内容、输出路径和验证结果。

## 完成条件

- 输出路径正确且未覆盖历史版本。
- 修改基于任务指定的最新有效产品。
- 当前轮全部材料已读取或如实报告未读取范围。
- validate 通过。
- 项目记录已更新。

{{BINDING_BLOCK}}
