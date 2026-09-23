# 干净环境「拉取 → 推送」复现记录（2026-09-23）

> 执行人：C ｜ 分支：`feature/c-clean-clone-check` ｜ PR 目标：`develop`
>
> **目的**：在一个**全新目录**（无历史、无配置、无追踪关系）里，从远端完整复现
> 「克隆 → 拉取 → 提交 → 推送 → 开 PR」，并核对
> `不推送/操作指导/` 下四份文档的描述与**远端实际状态**是否一致。
> 本文件只记录**仓库侧可复现的事实**，不含任何本机私有信息。

---

## 一、拉取结果（通过）

| 项 | 实测值 |
|:--|:--|
| 克隆耗时 | 6.0 s（68 个文件检出） |
| 被跟踪文件数 | **68**（文档记录为 51，见 §二） |
| 远端常驻分支 | `develop`、`main`，**两者 SHA 一致**：`55a3826` |
| 仓库默认分支 | **`develop`**（不是 `main`） |
| 克隆后的 HEAD | `55a3826`（= `origin/develop` = `origin/main`） |
| `git pull --ff-only` | `Already up to date.` |
| `refs/remotes/origin/*` | 两条**均正常落盘**（`develop` / `main`） |
| 工作区 | 干净，`git status` 无输出 |

远端身份核对（`gh api repos/… --jq '.full_name, .permissions'`）：
`lingxi-companion-team/lingxi-companion`，权限 `{"admin":true, …}`。

---

## 二、与文档记录的差异（文档数据为 2026-09-17）

| 项 | 文档（09-17） | 实测（09-23） |
|:--|:--|:--|
| 被跟踪文件数 | 51 | **68** |
| 顶层目录 | 无 `assets/` | **新增 `assets/`** |
| 工作区行尾 | 混用（14 CRLF / 18 LF / 19 空） | **全部 CRLF**（47 文件 `i/lf w/crlf`、21 空文件 `i/none w/none`） |
| `.gitattributes` | 不存在 | 仍**不存在** |
| 假冲突 | 0 | **0** |

> 行尾一栏的差异**不是问题**：全新克隆时 `core.autocrlf=true` 会把所有文本文件
> 统一转成 CRLF 落盘，所以"工作区全 CRLF"是克隆的**正常结果**；
> 索引侧仍是 100% LF。`git status` 干净，无假冲突。

### 关于「新克隆 vs 旧机器」的一个重要区别

文档 §一（`开发环境与冲突处理.md`）花了不少篇幅讲本机"`refs/remotes/` 为空、
分支没设 upstream"的两个隐性坏状态。**这两个问题在全新克隆里不存在** ——
`git clone` 会自动建好 `refs/remotes/origin/*` 并把默认分支的 upstream 设好。
也就是说：那两节是**修旧机器**用的，新机器/新成员可直接开工。

> 唯一的例外仍是排障手册 §2.4 那个**与网络无关**的假故障
> （`git fetch` 报成功但 `refs/remotes/` 始终为空，根因是运行环境的文件系统沙箱）。
> **本次未复现**该现象。

---

## 三、门禁核对（`develop`）

`gh api repos/{R}/rules/branches/develop` 返回的四条规则，与文档 §8.2 完全一致：

| 规则 | ruleset |
|:--|:--|
| `deletion` | 23449500 |
| `non_fast_forward` | 23449500 |
| `pull_request` | 23449500 |
| `required_status_checks` | 23449500 |

`required_status_checks` 的 5 个 context，与 `.github/workflows/tests.yml` 的 job
**逐字一致**：

```
guard / lint / typecheck / pytest (3.10) / pytest (3.12)
```

> 这是最值得定期复查的一处**隐形耦合**：CI 里改 job 名或 matrix 版本、
> 却没同步 ruleset，会让 required check **永远无法满足**，所有 PR 永久卡死。

---

## 四、本机连接注意事项

`github.com` 的通路**因机器/网络而异**，不要预设"直连一定可用"：

- 应先量、再下结论：`git ls-remote --heads origin` 连做 3 次；
- 若只报 **`CONNECT tunnel failed, response 502`** ⇒ 是**代理侧**问题
  （不是 GitHub 挂了，也不是凭据错——凭据错会返回 401/403），换出口即可；
- 若直连**21 s 超时** ⇒ 本机到 `github.com:443` 没有可用路由，
  此时"绕过代理"只会更糟，应显式**指定一个可用代理**。

⚠️ 一个反直觉点：**git 未必认 `export https_proxy=…`**。
实测在部分宿主里环境变量会被覆盖，而 **git 配置项优先级更高**：

```bash
git -c http.proxy=http://127.0.0.1:<端口> -c https.proxy=http://127.0.0.1:<端口> clone <url>
# 或在仓库内固化（仅影响本仓库）
git config http.proxy  http://127.0.0.1:<端口>
git config https.proxy http://127.0.0.1:<端口>
```

具体端口与本机排障细节见 `不推送/操作指导/github排障手册.md`（该目录不入库）。

---

## 五、结论

1. **干净环境下 `clone → pull → commit → push → PR` 通路正常**（本次实测），
   无需动用 Git Data API 兜底通道 —— 后者只在 `github.com` 完全不通时才是必需品。
2. 文档与远端状态的**唯一实质性偏差是文件数 / 目录（51 → 68、新增 `assets/`）**，
   属正常演进；规则、门禁、行尾结论均仍然成立。
3. 建议把上面的 5 项 required check 清单纳入**改 CI 时的强制核对项**
   （改动 `.github/workflows/tests.yml` 的 job 名或 matrix 版本时，同步核对 ruleset）。

---

*本记录由一次真实复现产生（干净目录、全新克隆、身份 C）。*
*若后续流程或保护配置有变更，请同步更新本文件并重跑一次复现。*
