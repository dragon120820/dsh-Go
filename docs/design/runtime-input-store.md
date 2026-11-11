# 固定运行时输入原件库

`runtime/lib/input_store.py` 保留构建所需的原始归档字节，使用原有 SHA256 / npm SRI 寻址。它以 UTF-8 读取 `runtime/build-runtime.sh` 的版本默认值、`PIN` 和源地址，以及 Ubuntu / npm 锁；不执行 shell，不另维护一份 pin，不发布到外部。

这是 Linux 构建主机工具，要求 Python 3.11 或更新版本。npm 预热另需已有的 host Node/npm；脚本不安装系统工具。原件库必须支持同盘硬链接，批次临时目录建在库内。这里的文件同步和原子替换保证正常错误路径不发布半文件，不宣称完整的断电持久性。

## 清单与原始字节

永久对象名为 `sha256-<小写十六进制摘要>` 或 `sha512-<小写十六进制摘要>`，没有扩展名。一个对象可以有多份锁或 ABI 引用；清单保留原 URL、来源位置、版本、ABI、npm 可选/平台字段及许可字段。未知许可用 `null` 和待核说明表示，不推定许可证。

默认清单覆盖当前两 ABI 与 x64 host Node：723 个对象，其中 62 个非 npm、661 个 npm。npm 两份当前锁共 662 条引用，包含全部 optional 平台包；不以采集机器实际会安装哪些包缩减备份。`--all-locks` 还纳入历史 DSH 锁，共 1061 个对象。数字对应本次固定源码，更新 pin/锁后由工具重新计算。

在仓库根目录运行：

```sh
python3 -I runtime/lib/input_store.py inventory > runtime-inputs.json
python3 -I runtime/lib/input_store.py inventory --all-locks > runtime-inputs-all.json
```

`--repo /absolute/repository` 可指定源码根。`--npm-lock /absolute/package-lock.json` 可重复使用，替换默认 npm 锁选择，非 npm 部分仍来自该仓库。默认和 `--all-locks` 都必须包含源文件声明的当前 DSH / pnpm 两锁，缺失即失败；当前 pnpm 锁的版本和 SRI 必须与 shell 的 `PNPM_VERSION` / `PNPM_INTEGRITY` 一致。显式 `--npm-lock` 仍是替换选择，不声称涵盖默认闭包。工具支持当前锁使用的单一 SHA256 / SHA512 SRI；缺 resolved、缺摘要、未知摘要算法或本地 link 条目明确失败，不能静默漏掉原件。PIN 块里的非注释未解析条目、尚无来源映射的新 pin 也明确失败；新增输入不能被默认清单静默略过。环境版本覆盖不会改变 inventory；它清点源文件中的默认版本。新版本应先更新源 pin/锁，重新生成清单和收集差集。

```sh
python3 -I runtime/lib/input_store.py import --store /absolute/input-store \
  /absolute/recovered/proot_5.1.107.98_aarch64.deb \
  /absolute/recovered/proot_5.1.107.98_x86_64.deb
python3 -I runtime/lib/input_store.py collect --store /absolute/input-store
python3 -I runtime/lib/input_store.py verify --store /absolute/input-store
```

`import` 按内容摘要识别所选清单中的原件，不依赖输入文件名；只处理显式给出的文件，不递归扫描其他目录。可先导入已恢复的原版 PRoot、旧构建缓存等，再 collect 缺失项。成功导入一部分不代表清单齐全；`verify` 逐项报告缺失/损坏，只有全齐且原摘要相符才退出 0。恢复到普通构建缓存由独立的构建读取接线（file:// CAS 回落）完成，恢复到 npm cache 由下述 warm-npm 完成，不再增加只转手的 restore 子命令。

`collect` 顺序为：校验过的本地对象 → 清单中的现有主源/已有官方回落 → 显式自留源。自留源可选：

```sh
python3 -I runtime/lib/input_store.py collect --store /absolute/input-store \
  --mirror-base file:///absolute/retained-originals
```

HTTPS 自留地址也接受同样的扁平对象名；URL 必须由 owner 明确提供，工具没有预置的公共自留库。`--mirror-base` 不允许账号密码、query 或 fragment。未配置时，JSON 的 `self_mirror` 明确写「自留回落不可用（未配置 --mirror-base）」；主源正常仍可成功，无法从主源取得的对象会列为错误，不能当作已归档。

下载先写库内临时文件；逐块计算原摘要、核对锁中已知大小，成功后 `fsync` 文件并 `os.replace` 到最终对象名。建立响应时的 HTTP 协议异常和读取中的传输中断均可继续下一来源；内容摘要/大小不符、坏的已存对象、写盘或替换失败立即停止本轮收集，不用下一来源掩盖错误。不可达对象会汇总报告，其他独立对象可继续收集。正常异常路径清理临时文件；进程被强杀可能留下 `.part-*`，它不被当作完整对象。

新写对象的流式读取有字节上限：锁中大小已知时以该值为限；未知时以 1 GiB（1,073,741,824 字节）为限，这是工具的传输边界，不是已测量的原件大小。最多多读一个字节即可判定超限，超出的块不写盘，临时文件清理，既有对象不替换；超限是内容错误，不回落其他来源。相同限制也用于 `import` 的新对象写入，已有对象的摘要复核不变。

所有子命令的 stdout 是一个 JSON 结果；错误时退出非零。`collect` 输出对象总数、缓存/新取数量、已取得字节数及错误；`verify` 输出逐项缺失/损坏。总字节是本轮实际核验对象的统计，不能据此宣称尚未收齐的全库大小。标准错误保留 npm 等诊断。

## npm 预热与原锁离线安装

构建接线仅在显式配置 `RUNTIME_INPUT_STORE_URL` 且已有 npm 锁时启用预热，未配置时保持原 npm 路径。shell 接线是单独的改动；此工具本身不改变默认构建行为。

```sh
python3 -I runtime/lib/input_store.py warm-npm \
  --repo /absolute/repository \
  --lock /absolute/repository/runtime/locks/dsh-0.2.2-rc.1/package-lock.json \
  --store /absolute/input-store \
  --mirror-base file:///absolute/retained-originals \
  --npm /absolute/host-node/bin/npm \
  --npm-cache /absolute/npm-cache \
  --userconfig /absolute/empty-user-npmrc \
  --globalconfig /absolute/empty-global-npmrc
```

两个 npmrc 必须已存在且为空。工具给 npm 的 PATH 优先放指定 npm 所在 bin 目录，沿用 host Node；每批最多 64 个归档，调用公开的 `npm cache add`。全部取到并核对原 SRI 后才开始 npm 缓存导入；失败返回非零，调用方不得进入 npm ci。工具不调用 npm ci、不改原锁、不开包脚本；调用方成功后以原有 flags 加 `--offline` 执行原 `npm ci`。

**npm 11.9.0 的实测约束：**直接把无扩展名 `sha512-<hex>` 交给 `npm cache add`，npm 将其当作目录，报 `DirFetcher` 错误。因此工具在库内为本批对象创建临时 `.tgz` 硬链接；npm 只读取这些已验证原件的同一 inode。批次结束或正常失败均移除临时目录，永久库仍只用摘要名，不额外存一份 tgz。没有硬链接支持时明确失败，不添加复制回退。npm 的 cwd/prefix 指向新建批次目录，user/global config 使用显式空文件，缓存路径由参数固定。

想完全离线使用时，先把原件库收齐或导入；已有对象经复核后不会联系来源。若库尚有缺失，工具仍按主源优先逐项尝试，显式配置自留源不等于禁止访问主源。原件库和 npm cache 是不同东西：后者可重新生成，不能替代留存原 tgz。锁中的 resolved URL 不改写。

## 给 owner 的不可变 release 准备入口

原件库位置已确定为**独立仓库的不可变 release**；仓库创建、发布、公开访问及不可变设置由 owner 操作。工具只生成本地待上传文件，不创建仓库、不上传、不改 5009 或构建默认行为。release 固定覆盖当前默认输入的全部 ABI、host Node 和 optional npm 包；这里不接受替换 npm 锁或缩减平台的参数。

**这是完成一次对应源码材料准备之后的一命令打包能力。当前仓库没有完整的源码来源清单，因此不能直接宣称已能生成真实全量 release。** 第一步只读现有 pin/锁，生成需要补材料的草稿：

```sh
python3 -I runtime/lib/input_store.py release-plan \
  > /absolute/reviewed-source-plan.json
```

在本次固定源码上，723 个原件中 641 个 npm 对象直接沿用锁里的身份和许可声明；62 个非 npm 对象及 20 个声明 LGPL/MPL 的 npm 对象形成 **54 组**缺口。同一非 npm 包的两个 ABI 共用一组，组内保留完整引用、版本、ABI、原摘要和来源位置。当前锁没有上游 repository/gitHead 字段，不能从 resolved tarball URL 推定对应源码。数字由当前 pin/锁计算，不是后续版本的常量。

自动分流只识别代码中明确列出的简单许可声明（MIT、ISC、Apache-2.0、BSD-2/3-Clause、0BSD、CC0-1.0、Unlicense、Python-2.0、BlueOak-1.0.0 及简单 AND/OR 组合）。它表示锁没有提示另补源码，**不证明归档内没有其他许可证或预编译组件**。发现此类组件时，可用 `release-plan --require-source sha256-…` 或 `sha512-…`（可重复）把相应对象也列为缺口。不要修改自动生成的 `id/inputs/refs/reason/source_candidates`；新增输入、变化的 pin/锁或漏掉必需组都会使后续准备失败。

可加 `--store /absolute/input-store`：对已缓存且原摘要通过的待审 npm tgz，只读 `package/package.json` 的 name/version/license/repository/gitHead，写入 `source_hints`；不取网络、不解包到目录、不开脚本。这些字段是**候选线索**，不能代替匹配二进制的证据。缺缓存就不提供线索。已 pin 的 PRoot 源码 ZIP 也只列为候选；它不自动证明 Termux 打包脚本、补丁和其余构建材料齐全。

### 最小源码材料清单（schema 1）

草稿保留 `inventory_sha256`，绑定工具按当前 pin/锁生成的整个 inventory；不是第二套运行时 pin。owner 只补真实缺口：各组的 `coverage`，以及需要新增的 `artifacts`。一个源码/构建材料对象可以由多个组共用，已有原件也可直接按 key 引用。

| 字段 | 必须提供的含义 |
|---|---|
| `coverage.license` | 核实后的许可或许可表达式。空值、UNKNOWN、NOASSERTION、PENDING 拒绝通过。不能把锁里已声明的 LGPL/MPL 等降成 MIT 来跳过材料。 |
| `coverage.sources` | 对应源码对象 key 的数组。要覆盖实际版本、所含组件和适用修改，不用最新版或只有补丁代替。 |
| `coverage.build` | 构建/打包/安装脚本、补丁、配置等材料的对象 key 数组。若已包含在源码归档内，可引用相同 key。 |
| `coverage.evidence` | 对应关系依据：精确版本/提交/官方构建记录、归档内许可与组件清单位置、各来源材料覆盖的范围。复合归档应列明组件及版本对应表的位置；不能只写“已确认”。 |
| `artifacts[].name` | 可移植的原文件名，仅作说明；上传文件仍使用内容 key。 |
| `artifacts[].sha256` / `size` | 经来源核验后固定的完整 SHA256 和准确正整数字节数。不能把随手下载后算得的摘要当作二进制对应关系的证明。 |
| `artifacts[].urls` / `origin` | 已核实来源的 HTTPS URL 数组，以及精确上游/构建出处文字。URL 不带凭据、query、fragment；仅有本地原件时 urls 可为空，用下述 `--import-file` 导入。 |

`coverage` 默认为 `null`，任何未补组都会在联网和建输出目录之前失败。源码义务未排除时，sources/build 都必须非空且引用实际存在的原件或材料；确认属于上述简单许可的未知项可以不加源码，不要求造一份材料凑数。新的材料必须被覆盖关系引用，重复对象、未引用材料、未知 key、缺摘要/大小都会失败。工具只检查声明与引用是否闭合，不能判断一段说明或一个归档在法律及技术上是否真的完整。

填写形状如下（尖括号是必须替换的说明，占位值会被校验拒绝，不是可发布材料）：

```json
{
  "license": "GPL-2.0-only",
  "sources": ["sha256-<已核实源码对象的64位摘要>"],
  "build": ["sha256-<已核实打包材料的64位摘要>"],
  "evidence": "精确构建提交/版本及对应记录；源码和补丁的覆盖范围；许可与组件表所在文件。"
}
```

GPL 对应源码的边界据 GNU 一手条文处理：GPLv2 §3 包括模块源码、相关接口定义及控制编译/安装的脚本；GPLv3 §1 说明生成、安装、运行和修改所需源码及控制脚本，§6(d) 说明在线分发的源码取得方式。LGPLv3 §4 还要按实际组合/链接方式确认相应材料。参见 [GNU 的 GPLv2 文本 §3](https://www.gnu.org/licenses/old-licenses/gpl-2.0.cs.html)、[GPLv3 §1/§6](https://www.gnu.org/licenses/gpl.en.html)、[LGPLv3 §4](https://www.gnu.org/licenses/lgpl-3.0.html) 和 [GNU FAQ：对应源码及构建脚本](https://www.gnu.org/licenses/gpl-faq.html#InstInfo)。本工具不把系统库例外自动套到整个 Ubuntu 基础归档，也不要求源码重建产生逐字节相同的二进制；对应关系及适用条款仍需 owner 核实。

本基底待补的实际材料包括：Ubuntu **原始基础归档内**的组件/版本及对应来源（不能只看额外 deb 锁或最终安装状态，基础归档里的旧版本仍在分发）；额外 deb 的精确 source package/version/打包材料；Termux 各包的对应源码、实际 recipe 与补丁；Node/Python 复合归档的组成资料；全部被留存的 LGPL/MPL 平台包。PRoot 的已有源 ZIP 不补齐这些缺口。未得到可信出处时保持 `coverage: null`，不能推测 URL、版本或摘要。

### 一命令收集、生成全部上传文件和自检

材料清单补齐并保存后：

```sh
python3 -I runtime/lib/input_store.py prepare-release \
  --plan /absolute/reviewed-source-plan.json \
  --store /absolute/input-store \
  --output /absolute/new-runtime-input-release \
  --import-file /absolute/recovered/proot_5.1.107.98_aarch64.deb \
  --import-file /absolute/recovered/proot_5.1.107.98_x86_64.deb
```

`--import-file` 可省略或重复，也可提供清单声明的源码/构建材料文件；按字节识别，不按文件名猜测。其后对缺失输入和材料按既有顺序收集、校验，可加显式 `--mirror-base`。源码材料必须预先声明准确大小，沿用传输上限、超限先拒绝及不绕过原摘要的规则；不执行源码、归档脚本或构建。

成功输出 `status: VERIFIED_DECLARED_MATERIALS`。`--output` 必须尚不存在，且与 CAS 目录互不包含；里面只有以下**完整上传集合**：

| 上传文件 | 内容 |
|---|---|
| 每个 `sha256-…` / `sha512-…` | 全部原件及新增源码/构建材料的原始字节，平面命名可直接给现有 CAS 回落使用。输出是独立副本，不和可变缓存共享硬链接。 |
| `runtime-input-release.json` | schema 1、`current-defaults` 范围、从当前源码计算的完整 inventory、补齐的 source_plan、每个数据文件的名称/实测大小/SHA256。原 SHA256/SRI 仍在 inventory 或 source_plan 中。 |
| `SHA256SUMS` | 对所有数据文件及上述清单的 SHA256；按惯例不包含自身。可用 `sha256sum -c SHA256SUMS` 检查传输字节。 |

上传前再离线复验，**使用单独保留的已核源码材料清单及固定源码**，不让待上传清单自行授权新的源码摘要：

```sh
python3 -I runtime/lib/input_store.py check-release \
  --plan /absolute/reviewed-source-plan.json \
  --output /absolute/new-runtime-input-release
```

检查包括全量当前 inventory/材料覆盖、准确文件集合、原摘要、实测大小、SHA256SUMS、清单与外部 source_plan 的一致性；缺文件、多文件、符号链接、目录代替数据文件或字节变化均失败。`bytes` 只统计已核实数据文件，不含两份元数据；`upload_files` 包含它们。成功还返回清单的 SHA256，供 owner 留存并对照上传。检查不会联网，也不会改变文件；同样输入和 source_plan 生成同样的清单。

准备过程中用 INCOMPLETE 标记；正常失败清理本次新建输出，已存在输出从不覆盖；中断遗留目录不能通过检查，应保留证据后选一个新的输出目录重试。文件同步沿用现有工具，未承诺整个目录的断电原子性。调用期间应独占操作 CAS/输出目录。成功只证明**已声明材料与文件字节一致**，不证明对应源码语义完整、许可证审查完成、远端已发布或 release 已不可变。owner 核实真实来源和覆盖范围、上传完整集合并设置不可变发布后，才有可配置的远端；5009 的暂缓状态不会被这个命令解除。

## 验证与发布边界

```sh
python3 -I runtime/tests/test_input_store.py -v
python3 -I runtime/tests/test_input_release.py -v
```

小测使用临时目录、合成字节、本地 file:// 来源、受控失败和已有 npm。真实 npm 集成从两个临时 tgz 开始，经无扩展名 CAS、工具临时别名、批量 cache add，再用不变的 resolved/integrity 锁执行 `npm ci --offline --ignore-scripts`，逐字核对锁与安装 payload。没有现成 npm 时该项明确 SKIP，可用 `INPUT_STORE_TEST_NPM` 指向已有 host npm；不为测试下载或安装 npm。

发布准备小测使用私有合成字节、临时本地来源和受控 HTTPS 响应，覆盖一次准备、独立副本、离线复验、缺口/漂移/篡改/缺失/多文件拒绝、坏源码/超限停止及正常失败清理；另实际检查当前 pin/锁生成的缺口数量。权限拒绝测试必须以非 root 用户运行，root 时明确 SKIP。这些夹具不是实际 GPL 源码，也不是实际原件库。

这些检查不代表 661 个真实 npm 对象已收齐或完整运行时构建通过。真实构建、release APK 及分层缓存复用按项目批次验证。工具不上传、不建 release、不绕过摘要；owner 自行发布。二进制、源码、许可证与构建材料的分发义务仍需逐组件确认，清单有 license 字段不等于完成审查。
