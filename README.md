# Generic Gradle cache / observer model

这是从零写的小型 Java + Kotlin mechanism model，**尚未执行任何 JVM、Gradle
或 JUnit**。源码和 workflow 是待独立审核的本地候选，不是发布或 CI 运行授权。
它不代表其他产品的 workload、总测试时间或性能收益。

## 两个独立问题

`:java-model` 提供简单字符串规范化，`:kotlin-model` 依赖它生成 label。
两个 module 各有两个普通 JUnit tests。固定 Gradle **8.14.3**、KGP **2.2.21**、
JUnit **5.13.4**、JDK **Temurin21.0.8+9-LTS**；CI selector 为
`21.0.8+9.0.LTS`。KGP 公开兼容表覆盖 Gradle 8.14 family；实际兼容性仍待 CI。
这里只各有一种语言 module，不模拟大型混合源码工程。

Compiler tasks 用标准 Gradle build cache。Test 的 up-to-date 与 result caching
均禁用，不能因为 compilation 命中 cache 就略过测试。
检查器同时要求当前 run 的四个 test-start/finish receipts、真实执行的 Test task
和完全一致的四个 JUnit XML cases；失败、skip、缺项、重复或旧 run 不能算成功。
原始 build exit 和日志保留，不自动重试。

另一个问题是 observer 自己的成本。`prepareFixture` 生成 128KiB 固定数据，
producer 完成后，`observeFixture` 才对这份本 task 内不变的 fixture 做32次观察：

| mode | fingerprint 次数 | 文件内容读取量 |
|---|---:|---:|
| `none` | 0 | 0 |
| `once` | 1 | 128KiB |
| `repeated` | 32 | 4MiB |

`once` 只复用本次 task 内这个不可变 fixture 的 fingerprint；不缓存任意源码、
classpath、JAR 或文件 size/mtime 的推断。源码、class、依赖都不在 observer 输入里。
这故意是很小的 synthetic work，不放大成产品收益。三种 mode 都有相同的最小
task timing 和 real-test receipts；`none` 不是完全零 instrumentation 的基线。
输出记录 work/bytes/nanoseconds，观察成本不当作免费。
configuration cache/parallel build 关闭，以免第一轮混入其他变量。

## Cache writer 与 reader

workflow 草稿仅对 main push、PR 到 main 和 manual 执行。发布前必须确认 default
branch 为 main；private repository 不选择 job，manual 可选三种 mode。
只有 default branch 的受信任 push/manual
能保存 cache；PR/其他 ref 一律 restore-only，Gradle local cache push 也关闭。
setup-gradle cache-disabled，setup-java 不启用 cache，不叠加隐式 writer。
只有 `.task-cache` 会跨 job 传递，不搬 workspace、`build/`、project `.gradle/`、
Kotlin incremental state 或用户 profile。依赖解析和 setup 下载成本仍独立存在。
GitHub restore action 精确命中和 prefix 命中分开看；Gradle 自己按 task inputs 判定
复用，源变更不需要把所有 task-cache entries 一起丢弃。

这是受审 workflow 的写入策略，不是防恶意 PR 修改 workflow 的绝对 cache ACL。
不使用 pull_request_target、不注入 secrets、不 checkout 其他仓库。
GitHub cache 是 branch-scoped：任意模拟 writer branch 不等价于 default main。
已保存的 key 不可变；同一 commit 重复 writer 不替换已有 key。

## 未来验证计划（未执行）

需另获资源授权后才运行。没有手造 wrapper JAR；workflow 用官方 setup-gradle
取得固定 Gradle。手动 Linux/Windows 使用同版本 JDK 和 `gradle`，示例：

```text
gradle --no-daemon --console=plain -PmodelRunId=trial-a -Pobserver=once -PtaskCachePush=true test observeFixture
```

1. 在新目录做 cold writer，保留 setup/fetch/build/test/observer/upload 成本。
2. 另一个全新 checkout 只 restore task cache，运行同源码，核 compileJava/
   compileKotlin/compileTestJava/compileTestKotlin 的实际 FROM-CACHE 与原输出，
   两个 Test 必须仍 EXECUTED，四个 cases 必须重新开始/结束。
3. 分别改 Java body、Kotlin body、public API、test、编译选项或删除源文件；
   每个 variant 与同源码 no-build-cache clean build 比较。变化的输入应失效，
   无关 task 不预设必须重编；ABI avoidance 与源码增量不是同一机制。
   删除源后核旧 class 不残留。native failure/invalid 计入原分母，不补绿。
4. cache correctness 成立后，另做 none/once/repeated 的有界交替重复。
   先校准一次，再另授权每种3次；原始逐次成本全报，不能只挑最快值。

本原型不实现分片。以后如果增加，先取得无分片 case 全集，再证明 shard union
完整、交集为空和真实执行；不能拿更少 tests 的更短耗时当加速。
workflow 只有单 Ubuntu job，timeout10min，无 schedule、无自动重跑，
`if: always()` 尝试上传小型 JUnit/JSON/日志；runner 被强杀时仍可能缺最后输出。
七个 action uses 已固定到官方仓库 major release 对应的实际 commit SHA；
已检查 pinned action manifests 的 Git blob bytes、入口与本 workflow 使用的 inputs。
这不是对第三方 bundled JavaScript 全部实现的形式化审核。

`DEPENDENCIES.json` 绑定本源码的有限 public-byte review：Gradle/JDK 官方 archive
SHA-256、Python 指定平台 release asset digest、声明的 KGP/JUnit POM bytes 与
可取得的 publisher sidecars。KGP 的 SHA-256 sidecar 不可得，只有 legacy SHA-1
匹配，不能声称同等抗碰撞保证。工具 archive/JAR bytes 没有下载或执行；
checksum 来源与文件同站点，不能当独立 publisher 身份认证。
记录的 archive digests 是审查依据，不是额外实现的 workflow runtime 校验门。

没有生成虚假的 Gradle verification metadata 或传递依赖白名单。
一次另授权的 cold bootstrap pilot 只能核 resolver 实际选出的 variant/artifact、
版本、Gradle DSL、四个真实 JUnit cases 和 observer 输出。首次取得完整 dependency
graph 后应独立审核再决定验证 metadata；pilot 的首次网络解析仍是明确剩余信任边界。
它不能顺带证明 cache invalidation、性能、稳定性或模型以外的工程效果。

## 可离线检查与公开边界

只测 Python 检查器（synthetic receipts，不执行 Gradle/JVM）：

```text
python -B -m unittest discover -s tools -p test_check_evidence.py
```

本地 pure helper tests 通过不等于 Gradle DSL、compiler、Test listener、cache-hit 或
native performance 已验证。后续 Git 作者只用公开 handle/noreply；本候选没有
Git history、个人姓名/邮件/机器路径、真实工程源码或上游实现拷贝。
MIT 只覆盖这里新写的源码；公开依赖保留各自许可。

公开依据：
[Gradle8.14.3 build cache](https://docs.gradle.org/8.14.3/userguide/build_cache.html)、
[TaskOutputs](https://docs.gradle.org/8.14.3/javadoc/org/gradle/api/tasks/TaskOutputs.html)、
[KGP compatibility](https://kotlinlang.org/docs/gradle-configure-project.html)、
[JUnit5.13.4](https://docs.junit.org/5.13.4/user-guide/)、
[GitHub cache scope](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)。
