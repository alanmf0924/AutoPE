# AutoPE 共享测试资源与交付清单（2026-10-16）

## 用途与当前边界

本清单用于完成 OpenSpec `add-platform-authentication` 的 4.1–4.3：在独立共享测试环境验证真实账号、授权、审计、迁移、备份恢复和 Worker 故障。第一轮默认不调用 Dify、DeepSeek 或其他付费模型，不接正式流量，也不执行 Dify 生产发布。资源创建、真实凭据接入、外部调用和上线分别由相应负责人批准。

按“不办理工信部 ICP 备案、使用国内支付渠道”的偏好，推荐阿里云**中国站账号 + 中国香港地域** ECS 和 RDS PostgreSQL，按量付费先建共享测试。中国站可购买香港 ECS 并以人民币付款，账户充值可使用支付宝、银联或网银；实际支付方式以账号页面为准。若已有受控香港 Linux 主机和专用 PostgreSQL，可复用现有设施。规格是初始估算，须按实测内存、任务并发和预算调整。

中国香港服务器上的网站无需工信部 ICP 备案。**这不等于面向中国内地公开提供服务时完全没有其他备案义务**：阿里云文档提示，境外/香港网站若在中国内地可访问，仍可能需要公安联网备案。本阶段建议仅供授权人员通过 VPN 进入香港 VPC 内网测试，网页不对公网开放；可用私有 DNS/分流解析和域名验证取得 HTTPS 证书。如以后对公网开放或服务中国内地用户，先由负责人核实适用要求。不要接入要求 ICP 备案的中国内地 CDN/加速节点。

## 一、现在需要准备的资源

| 项目 | 推荐获取处 | 建议选择/配置 | 交付给实施者的内容 | 验收要点 |
| --- | --- | --- | --- | --- |
| 云账号与权限 | 阿里云中国站账号 / RAM | 独立项目标签、预算负责人；给部署者最小所需 RAM 权限，不共享主账号密码；账号先确认支付宝/银联付款可用 | 账号归属、部署者身份、预算上限、资源命名规则 | 能创建和查询专用香港测试资源；能追溯操作人 |
| 应用主机 | 阿里云 ECS，中国香港 `cn-hongkong` | Linux x86_64，初始按 4 vCPU / 8 GiB、约 80 GiB 系统盘评估；与 RDS 同地域、同 VPC；按量付费，勿用可随时回收的抢占式实例承载状态服务 | 地域、实例 ID、VPC/交换机、安全组、管理入口、维护窗口 | 仅受控管理员可登录；运行前端、API 和独立 Worker；CPU/内存/磁盘可观测 |
| PostgreSQL | 阿里云 RDS for PostgreSQL，中国香港 | 专用实例或经批准的专用数据库；优先 PostgreSQL 16（本地迁移预演版本，购买页核对可选规格）；初期可评估基础版，若要求时间点恢复则选支持日志备份的版本；与 ECS 内网互通，不开放公网数据库端口 | 实例 ID、内网连接地址、数据库名、应用账号名、备份策略、恢复责任人；密码另走安全渠道 | Alembic 升到 `20261015_22`；旧证据读回；独立恢复库核对摘要 |
| 域名与 HTTPS | 已有企业域名 + 私有 DNS/分流解析 + 证书管理 | 一个测试子域名，例如 `autope-test.example.com`；VPN 内解析到私有地址，同域名承载网页与 `/api`；证书覆盖该域名，明确续期负责人 | 域名、DNS 管理人、证书部署方式和续期计划 | 浏览器证书有效；Cookie 为 Secure/HttpOnly/SameSite；API 同源 |
| 入口与网络 | 香港 VPC + 现有企业 VPN；没有现成连接时评估阿里云 SSL-VPN 网关 + ECS 安全组 + Nginx/Caddy | 443 仅从 VPN 私有网段可达；管理端口仅授权人员可达；数据库、Worker、Secret 目录不对公网开放；不接中国内地 CDN | VPN 管理人、允许访问人员/网段、管理端口策略、Dify 沙箱网络路径 | 公网不能直接访问测试平台、PostgreSQL、Worker、Secret；授权人员经 VPN 能访问 HTTPS |
| 数据备份 | RDS 备份策略及独立恢复目标 | 自动备份和保留期由负责人确认；迁移前手动备份；准备独立恢复库/实例，演练后按约定保留证据 | 备份点 ID、保留期、恢复目标、保管人与恢复操作人 | 在独立目标恢复，迁移版本、账号、角色、审计和业务对象摘要一致 |
| 监控告警 | 阿里云 CloudMonitor + 主机进程/应用日志 | ECS、磁盘、RDS、HTTPS、API、两个必需 Worker 心跳与任务积压/失败告警；指定接收人 | 告警接收人、渠道、阈值、值班/处理人 | 人工触发一次安全的测试告警并收到通知 |

阿里云官方资料：[ECS 创建与计费](https://www.alibabacloud.com/help/en/ecs/quick-start)、[RDS PostgreSQL 创建](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-postgresql/create-an-apsaradb-rds-for-postgresql-instance-1)、[RDS 备份](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-postgresql/back-up-an-apsaradb-rds-for-postgresql-instance)、[RDS 恢复](https://www.alibabacloud.com/help/en/rds/apsaradb-rds-for-postgresql/restoration-2/)、[DNS 记录](https://www.alibabacloud.com/help/en/dns/pubz-add-parsing-record)、[证书](https://www.alibabacloud.com/help/en/ssl-certificate/what-is-an-ssl-certificate)、[监控告警](https://www.alibabacloud.com/help/en/cms/cloudmonitor-1-0/user-guide/create-an-alert-rule)。实际价格按购买页地域和规格核算，当前不填写未经核实的报价。

地域与付款核对：[阿里云中国站跨地域购买](https://help.aliyun.com/zh/ecs/cross-region-usage-faqs)、[香港 ECS 地域](https://help.aliyun.com/zh/ecs/user-guide/regions-and-zones)、[支付宝/银联充值](https://help.aliyun.com/zh/user-center/add-funds/)、[香港节点备案边界](https://help.aliyun.com/zh/icp-filing/basic-icp-service/user-guide/icp-filing-application-overview)、[香港 RDS PostgreSQL 支持范围](https://help.aliyun.com/zh/rds/apsaradb-rds-for-postgresql/assured-serverless-apsaradb-rds-for-postgresql-instances-in-public-preview/)、[SSL-VPN 接入 VPC](https://help.aliyun.com/zh/vpn/sub-product-ssl-vpn/getting-started/connect-a-client-to-a-vpc)。

## 二、部署负责人在服务器上准备

- Python 3.12 或更新版本、`uv`、Node 24 构建环境；前端构建产物与 API 经同一个 HTTPS 域名提供。Node 只需用于构建，生产入口使用静态文件和反向代理。
- 独立进程/服务：FastAPI、`app.evaluation_worker`、`app.candidate_worker`；若测试 AI 辅助标注，再启动 `app.review_ai_worker`。API 和 Worker 使用同一数据库与同一部署配置，不共用同一个 Worker 进程。
- 仅服务账号可读取的绝对路径 Secret 目录，例如 `/var/run/secrets/autope`。Dify Key 以 `secret_ref` 对应的只读文件交付；平台现有解析器读取**文件**，不能直接读取云 KMS API。初期可由服务器负责人安全挂载；若以后采用 KMS，需另建受控同步/挂载桥接并验证轮换，不把“购买 KMS”当作已集成。
- 外部注入的会话密钥（至少 32 字符）、PostgreSQL 连接凭据，以及部署/恢复账号；真实值不得进入仓库、示例配置、工单、普通聊天、日志或截图。
- 迁移前备份与可回退的应用版本。已经创建真实账号后，不以 Alembic 降级删除身份表作为回退；不可兼容时进入只读维护。

共享测试的关键配置名（这里只记录应有值或形式，不填写密钥）：

```text
AUTOPE_ENVIRONMENT=shared_test
AUTOPE_AUTH_DISABLED=false
AUTOPE_SEED_DEMO_DATA=false
AUTOPE_DATABASE_URL=postgresql+psycopg://<securely-injected-credentials>@<private-rds-host>/<dedicated-db>
AUTOPE_ALLOWED_ORIGINS=https://autope-test.example.com
AUTOPE_SESSION_SECRET=<securely-injected-secret-at-least-32-characters>
AUTOPE_SECRET_RESOLVER=external
AUTOPE_EXTERNAL_SECRET_DIR=/var/run/secrets/autope
AUTOPE_REQUIRE_WORKER_HEARTBEAT=true
```

先备份并迁移数据库，启动评测 Worker 和候选 Worker，再启动 API 和 HTTPS 入口。当前代码在 `shared_test`/`production` 下仍让 `/api/readyz` 固定返回 503，属于未放行保护；共享测试需要受控入口单独访问，不能把 503 改写成“已就绪”。门禁证据齐备后再评估解除保护与流量切换。

## 三、需要人提供或确认的信息

**采购前最少确认这 8 项：**

1. 云账号归属、预算上限、采购/部署负责人，以及是否已有可复用的受控 Linux 主机和专用 PostgreSQL。
2. 共享测试访问范围：人员名单、VPN 与私有 DNS 管理人、维护窗口；域名与证书管理人。若计划向中国内地公开提供网站服务，另由负责人核实公安联网备案等要求。
3. ECS/RDS 地域、VPC、网络连通负责人；若后续连接 Dify，隔离 Dify 沙箱位于哪里、如何安全互通。
4. PostgreSQL 专用数据库、备份保留期、独立恢复目标、备份保管人和恢复操作人。
5. 首位平台管理员及服务器恢复责任人；至少两名**不同的真实测试人员**用于登录和独立审批。
6. 五类业务角色 `operator`、`annotator`、`prompt_owner`、`approver`、`auditor` 的人员映射；平台管理员是否兼职，以及同人自审冲突范围。
7. 只用于共享测试的虚构/脱敏数据范围、数据提供者、审查者和保存位置；旧本地记录是否需要迁移或保持归档。旧 `local_unverified` 身份不能自动当作新账号批准。
8. 故障演练中止条件、可兼容回退版本、只读维护负责人、告警接收人和最终验收签字人。

**请只交付非敏感元数据。** 密码、数据库连接密钥、Dify/模型 API Key、初始化令牌、Cookie、证书私钥由负责人通过受控终端或 Secret 挂载交付，不贴在清单、聊天或截图中。

## 四、真实 Dify/模型联调时另行准备

身份与恢复验收本身不需要调用模型。若之后做真实评测，再单独确认：

| 内容 | 谁提供 | 需确认的字段/证据 |
| --- | --- | --- |
| 隔离 Dify 基线与候选应用 | Dify 负责人 | 两个不同 App ID、已发布 Workflow UUID、运行路径、可达地址、输入变量映射、版本锁定证据；不得把生产应用当隔离候选 |
| Dify 凭据 | Secret 管理/部署负责人 | 测试应用专用 Key 的 `secret_ref` 与只读挂载位置；不在页面内联填写共享测试 Key |
| 黄金集 | 业务负责人 | 冻结版本、用例数量、风险等级、预期契约、可执行断言覆盖率、批准人；无断言的语义条目预留人工复核 |
| 模型外传 | 业务/数据负责人 | 发送哪些字段到哪个模型/地址、是否脱敏、每类最多调用次数、费用上限、超时与终止条件；DSPy 与 AI 辅助标注分别批准 |
| 发布边界 | 审批人 + Dify 负责人 | 候选报告及独立审批、实际 Dify 控制台操作者、工作流版本回填；平台只记录交接，不自动发布 |

## 五、验收交付物

- **4.1 身份与授权：**真实双账号首次设置/登录；Cookie、CSRF、401/403、停用撤权、五类角色入口和直接 API 越权；不同主体的测试集/报告审批；`If-Match` 过期拒绝；数据库中 `local_verified` 审计读回。
- **4.2 恢复与故障：**会话存储故障时写入失败关闭；重复初始化拒绝；Worker 故障不变成模型质量失败；独立数据库备份恢复；兼容版本回退或只读维护演练。保存时间、执行人、请求 ID、状态码、对象摘要和脱敏日志。
- **4.3 门禁汇总：**自动测试、迁移、浏览器和共享测试证据串联；列明残余风险、业务/部署审批。`/api/readyz` 当前固定 503，在解除保护并取得正式批准前不切流量。

详细执行顺序与停止条件见 `docs/SHARED_TEST_AUTH_ACCEPTANCE.md`，部署命令与身份恢复见 `docs/SELF_HOSTED_DEPLOYMENT.md`。
