# Identity 与 Namespace 模型

Mems 使用请求中的 identity 字段确定记忆归属边界。API 接受可选的通用
`namespace` 路径（`string[]`），用于表达调用方的业务上下文，例如
`["projects", "prj_abc123"]`。Mems 将经过验证的 identity 与这些路径段组合，生成
不可变的内部 `MemoryNamespace`；调用方不传完整内部值。

Namespace 是存储分区标识，用于让不同适配器采用一致的命名规则；业务授权仍由
调用方应用负责。应用需要认证调用者并判断其可使用的 identity，Mems 再将该
identity 一致地应用到 SQL、Redis 和 Qdrant 操作中。

## Identity 字段

| 字段 | 类型 | 角色 |
| --- | --- | --- |
| `tenant_id` | string 或 null | 租户 / 组织边界 |
| `user_id` | string 或 null | 用户边界 |
| `agent_id` | string | Agent 边界，必填 |
| `session_id` | string 或 null | 写入侧溯源，不作为召回边界 |

`tenant_id`、`user_id`、`agent_id` 是身份维度。多用户部署中，调用方应用应从可信
的认证与授权上下文生成这些字段，不应直接信任客户端任意提交的值。可选 identity
字段缺省时会进入独立的空值身份分区，不会作为通配符，也不代表授权。

## MemoryNamespace

`MemoryNamespace` 是 Mems 根据经过验证的 identity 和可选请求 `namespace` 路径段
生成的不可变值对象，规范路径带版本号：

```text
("v1", "tenant", <tenant_id>, "user", <user_id>, "agent", <agent_id>, "memories")
```

请求 `namespace` 是不透明路径段列表，不是完整存储 namespace，也不代表权限。省略或
传空列表时使用该 identity 的默认分区，不会自动搜索子 namespace。写入与查询需传入
相同路径。实现必须让可选 identity 的缺省值与
真实值可区分，并避免路径段序列化歧义。记忆类型、时间、重要度和证据链继续作为记录
属性，便于一次召回跨类型检索。

例如，调用方可以传 `["projects", "prj_abc123"]`；应使用稳定标识，而非可能变化的
展示名称。Mems 不为这些路径段赋予产品语义，也不增加 `project_id` 等专用字段。

值对象应使用不可变的 tuple 保存路径段，例如：

```python
class MemoryNamespace(BaseModel):
    model_config = ConfigDict(frozen=True)
    segments: tuple[str, ...]
```

长期记忆 namespace 不包含 `session_id`，因此可以跨会话召回。L0 可将
`session_id` 加入按会话组织的缓存键。

## 授权与共享记忆

Mems 不定义应用角色、团队成员关系或 ACL 规则。访问个人记忆时，调用方应用负责
授权用户并传入对应 identity。访问团队或应用共享记忆时，调用方先授权该共享空间，
再传入代表该空间所有者的 identity。自由格式的可见性标签本身不构成授权检查。

调用方应用必须在传入 namespace 路径前授权对应业务上下文。Mems 将其作为存储分区选择
器，而不是 ACL。召回按 identity 与 namespace 精确匹配，不隐含通配或前缀检索。项目
共享记忆还必须使用代表共享所有者的 identity；仅 namespace 相同不会跨越 tenant、user
或 agent 边界。

## 与存储层的关系

```text
L0 Redis key   ：namespace + session_id
L1 SQL         ：tenant_id / user_id / agent_id / session_id + namespace
L2 SQL         ：tenant_id / user_id / agent_id + namespace
Qdrant payload ：identity 与 namespace 过滤向量候选
L3 JSONL       ：原始 identity 字段与归档溯源
```

Identity 列继续保留，用于显式过滤、索引、报表和迁移。`MemoryNamespace` 提供
统一的规范分区表示；它不替代 identity 字段，也不能单独授予访问权限。

## 检查清单

- [ ] 所有写入与召回使用从可信应用上下文生成的 identity。
- [ ] `MemoryNamespace` 由 Mems 根据经过验证的 identity 与 namespace 路径段生成，创建后不可变。
- [ ] SQL、Redis、Qdrant 使用相同的 namespace 规范。
- [ ] 可选 identity 维度缺省时的行为已明确且经过审慎设计。
- [ ] `session_id` 仍只用于溯源，不用于长期召回分区。
- [ ] 访问共享记忆前由调用方应用完成授权。

更多内容见 [Memory Recall Protocol](/zh/introduction/memory-recall-protocol)
与 [接入总览](/zh/integration/overview)。
