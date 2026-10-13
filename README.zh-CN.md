# SSD Lab

一个 macOS SSD 性能与健康工具。默认英语界面，支持八种语言，使用真实 fio 执行文件读写测试，使用 smartctl 读取 SMART。参考 CrystalDiskMark 的负载参数与 CrystalDiskInfo 的信息展示方式，独立开发，与 Crystal Dew World 无隶属关系。

## 1.3.1 Apple NAND 与 SMART 修正

- 在系统公开字段的 Apple 内置 SSD 上，显示 NAND 供应商、原始供应商名称、NAND 描述及信息来源。通过只读 IOKit API 定位所选设备，并沿其父节点读取 `Controller Characteristics.vendor-name`，避免多盘连接时匹配错盘。
- 支持显示三星、东芝／Kioxia、SK hynix、美光、YMTC、SanDisk 等系统报告的名称。这个名称映射不表示这些厂商都存在于 Apple 原厂配置中。不会通过 `AP…` 型号、PCI Vendor ID 或未经验证的 manufacturer-id 字节猜测供应商。未公开与读取失败分别显示。
- 默认 SMART 读取改为 `smartctl -i -H -A -j`；附加错误日志需单独勾选读取。健康数据和附加日志分别保存，附加日志读取失败不改变健康检查结果，详细返回信息仍可查看。
- SMART JSON 导出包含 `smartctl` 原始健康数据与 `ssd_lab` 扩展信息（NAND 来源、附加日志）。

Apple SSD 出现 `Read 1 entries from Error Information Log failed … code=745` 时，“1”是尝试读取的条目数，不是已确认的磁盘错误数。745 对应 IOKit `kIOReturnDeviceError`（0xe00002e9），仅凭此通用错误码无法确定底层原因、权限不足或硬盘故障。健康检查、Critical Warning、介质错误计数仍需独立判断；软件保留真实告警与原始返回信息。

调查依据：[Apple Silicon 实机 IORegistry 测量](https://github.com/ThomasKaiser/Knowledge/blob/master/articles/Exploring_Apple_Silicon_on_MacBookAir10.md)、[smartmontools 维护者对相同错误的说明](https://listi.jpberlin.de/pipermail/smartmontools-support/2022-November/000926.html)、[Apple IOReturn 定义](https://github.com/apple-oss-distributions/xnu/blob/main/iokit/IOKit/IOReturn.h)。该驱动字段不是稳定公开 API，能否读取取决于硬件与系统版本。

## 1.3 外观与多语言

- 三套可即时切换的主题：日间、夜间、Miku（青绿与粉色）。主题和语言偏好保存在本机，初次运行默认英语与日间主题。
- 八种界面语言：English、简体中文、繁體中文、日本語、한국어、Deutsch、Français、Español。导航、配置、SMART 字段、图表坐标轴、提示及原生菜单随语言切换，测试参数和成绩不会丢失。
- macOS 26／27 使用 AppKit `NSGlassEffectView`；较旧系统回退为 `NSVisualEffectView`。玻璃用于窗口材质、导航和控件，表格与图表保持清晰。遵循减少动态效果、降低透明度及增强对比度设置。
- 原始 fio / smartctl 输出、设备标识、文件路径、技术名称和报告数据保留原貌。语言切换不会修改磁盘或触发测试。
- UI 材质参考 [Apple Materials](https://developer.apple.com/design/human-interface-guidelines/materials)、[Liquid Glass](https://developer.apple.com/documentation/appkit/nsglasseffectview) 和 [macOS 27 AppKit 更新](https://developer.apple.com/videos/play/wwdc2026/289/)。

## 散点图与 POSIX 引擎

“散点分析”页可自定义 X / Y 字段、线性 / 对数尺度、最小 / 最大范围、读写方向及测试项目。悬停查看点的数值，导出 PNG 或当前筛选数据的 CSV；历史记录也有“散点”入口。

- 延迟日志：相对时间、fio 总 I/O 延迟、队列深度、线程数、轮次；逐 I/O 模式还提供**测试文件偏移**和 I/O 大小。文件偏移不是物理 LBA，窗口模式没有可解释的单次偏移，因此不会生成假偏移点。
- 带宽 / IOPS 日志：时间、MB/s、IOPS、设定 QD、线程和轮次；默认使用 fio 10 ms 窗口日志，数值按工作线程记录。逐 I/O 模式根据已完成 I/O 计算 100 ms 窗口的带宽与 IOPS，末尾窗口按日志跨度计算，属于日志派生值。
- 逐轮汇总：轮次、QD、线程、带宽、IOPS、平均延迟、P99 / P99.9 / P99.99。
- 默认 10 ms 采集：延迟点是窗口平均值；不能从这些平均值重新计算尾延迟百分位。
- 可选逐 I/O：`log_avg_msec=0`、`log_offset=1`，每项合计最多 20 万次 I/O，关闭预热，达到上限可能提前结束该项。适合观察单次 I/O 与文件偏移关系。
- 大日志使用蓄水池抽样并额外保留最慢 20 点，整次测试限制保存的绘图点数；界面公开原始行数、所绘点数和抽样方式。图中点的数量比例不是原始分布，百分位始终使用 fio 的完整统计。
- 临时 fio 日志解析后删除；完整 JSON 报告保留 fio JSON+ 统计及所保存的绘图样本。CSV 图数据同样是保存的样本，不冒充完整逐 I/O 日志。
- 日志会增加内存 / CPU / 文件输出开销，可能影响测量，比较时保持相同采集方式。图的时间原点是每项第一个日志事件。

fio 的实际 POSIX 引擎名称为 `posixaio`（POSIX 异步）与 `psync`（pread / pwrite 同步），另提供 `sync`（read / write）。同步引擎只允许每线程 QD1：选择同步引擎会切换到专用 Q1 配置；后端会拒绝把同步引擎当作高 QD 扫描。自动模式下，macOS 标准 / NVMe / QoS 配置使用 posixaio，POSIX 同步配置使用 psync。

## 专业测量补充

本版根据厂商公开资料与行业规范补充了 QoS 测试、统计指标和方法边界。应用“测试方法”页提供原始资料、论文链接与阅读说明。

- QoS 配置：4 KiB 随机 **70% 读 / 30% 写**，QD1 / QD4 / QD16 / QD32，单线程。读写同时发生，界面分别展示两方向成绩；同一项的最佳读写成绩来自综合带宽最高的同一轮。
- fio 总延迟：P50、P95、P99、P99.9、P99.99、P99.999 和最大值，JSON 中保留原始分布。界面“逐轮结果与尾延迟”保留每一轮，P99.99 尾部预计少于 10 次 I/O 时提示样本偏少；此提示不是置信区间或认证结论。
- 汇总保留带宽平均值、中位数、最小值、最大值、样本 CV；不平均多个任务或不同轮次的百分位。缺乏可合并分布时不虚构百分位。
- JSON 报告记录系统、架构、CPU、内存（可获取时）、工具版本、剩余空间及测试范围。明确 `file_level`、未做设备清除 / 全盘预处理、未验证设备稳态、未测量 PCIe 链路 / 功耗。
- CSV 增加尾延迟、中位数、CV 与稳态验证状态。快速模式仍保留类似 CDM 的最佳轮次视图，完整结果应结合逐轮记录解读。

当前模式不宣称通过 SNIA / OCP 规范，也不能通过短时小文件测试确认 SLC 缓存已经耗尽。消费级缓存行为、热降速、企业级稳态、数据保持和掉电保护属于不同实验，需要独立测试条件。

直接参考的公开资料包括：

1. [三星 PM863 白皮书](https://semiconductor.samsung.com/resources/white-paper/PM863_White_Paper.pdf)：公开 fio、设备预处理、混合负载、队列扫描和 QoS 的实际例子。不要将其中一种产品的条件当作所有三星 SSD 的通用规范。
2. [三星 990 PRO 数据表（2022）](https://download.semiconductor.samsung.com/resources/data-sheet/Samsung_NVMe_SSD_990_PRO_Datasheet_Rev.1.0_10119997004809.pdf)：Iometer、指定主机、辅助盘与 TurboWrite 条件。
3. [SK hynix eSSD 虚拟化技术说明](https://news.skhynix.com/en/rulebreakers-revolutions-essd-virtualization-for-big-data/)：多个工作负载下的性能一致性。
4. [SNIA SSS PTS 2.0.2（2020）](https://www.snia.org/sites/default/files/2025-02/SNIA-SSS-PTS-2.0.2.pdf)：设备级测试流程和报告方法。
5. [OCP Datacenter NVMe SSD 2.7](https://www.opencompute.org/documents/datacenter-nvme-ssd-specification-v2-7-final-pdf-1/)：设备内部延迟监测与异常诊断。

建议论文：

- [Practical Erase Suspension for Modern Low-latency SSDs，ATC 2019](https://www.usenix.org/conference/atc19/presentation/kim-shine)：含三星作者；研究擦除与混合负载尾延迟，部分评估采用经实机校验的模拟器。
- [A New LSM-style Garbage Collection Scheme for ZNS SSDs，HotStorage 2020](https://www.usenix.org/conference/hotstorage20/presentation/choi)：含 SK hynix 作者；使用研究型 ZNS 原型，讨论利用率、更新模式和垃圾回收开销。
- [Design Tradeoffs for SSD Performance，ATC 2008](https://www.usenix.org/legacy/events/usenix08/tech/full_papers/agrawal/agrawal_html/index.html)：建立 SSD 内部并行与工作负载的基础分析框架。
- [Reducing Solid-State Drive Read Latency by Optimizing Read-Retry，ASPLOS 2021](https://arxiv.org/abs/2104.09611)：NAND 读重试与读取延迟机制。

这些论文用于理解和设计实验，不是厂家完整量产验收流程。应用设计建议是工程推论，资料作者未对本软件进行认证或背书。

## 运行

本次提供的 **SSD Lab.app** 是 Apple Silicon（arm64）版本，目标系统 macOS 13 或更新，已在当前 macOS 环境验证。内置 Python、fio 3.43 和 smartmontools 7.5，打开应用即可使用，不需要安装 Homebrew 或 pip 包。

1. 双击 `SSD Lab.app`。
2. 选择被测 SSD 中一个可写目录，例如外置 SSD 的专用测试目录。
3. 选择配置、测试文件大小、轮数、时长，点击“开始全部测试”或单项测试按钮。
4. 在“磁盘健康”扫描设备并读取 SMART；需要时打开“管理员权限”，通过 macOS 系统授权。密码由系统处理，应用不收集密码。

应用使用本地临时文件进行测试。准备阶段会先写入完整测试文件，即使只关注读取成绩也会产生准备写入。运行时间包含每项 1 秒预热；测试文件大小限制占用空间，**不限制测试累计写入量**。测试完成、停止或普通程序退出后清理自己的临时目录。意外断电或强制杀进程可能留下 `.ssd-lab-*` 目录，可在确认应用已退出后删除该目录。

应用采用本地临时签名，尚未做 Apple Developer ID 签名及公证。打包版本仅面向当前 Mac 和本地使用；跨设备分发前建议正式签名、公证并实机验证。

## 功能

- 标准配置：SEQ1M Q8T1、SEQ1M Q1T1、RND4K Q32T1、RND4K Q1T1。
- NVMe 配置：SEQ1M Q8T1、SEQ128K Q32T1、RND4K Q32T16、RND4K Q1T1。
- 测试文件：64 MiB / 256 MiB / 1 GiB / 4 GiB / 16 GiB；1 / 2 / 3 / 5 轮；单项 1 / 3 / 5 / 10 / 30 秒。
- MB/s、IOPS、平均总延迟 μs 三种显示单位；每项取带宽最高轮次，同一轮的 IOPS、延迟一并显示。单项也包含读写两个方向。
- 可停止测试、实时阶段与估算进度、详细日志、临时文件清理。
- SMART 健康状态、NVMe 估算剩余寿命、温度、型号、序列号、固件、容量、通电次数/时间、NVMe 主机读写量、NVMe / ATA 属性与原始 JSON。
- 最近 100 次记录，CSV 摘要导出、包含命令及 fio 原始 JSON 的完整报告导出、SMART 原始 JSON 导出。
- 自动检测工具，允许指定 fio / smartctl 绝对路径。

## 测量边界

**这些是 fio 测试成绩。** 负载参数参考 CrystalDiskMark，但执行引擎、文件系统、系统缓存、CPU、加密和后台负载不同，成绩不保证与 CrystalDiskMark 相同。macOS 使用 `posixaio`，实际队列深度可能受系统限制；完整 JSON 中保留 fio 的 `iodepth_level` 分布。

MB/s 按 1,000,000 字节换算，容量按 MiB / GiB 表示。平均延迟使用 fio 的 `lat_ns`，包括提交与完成延迟，转换为 μs。多线程按总 I/O 次数加权。NVMe 线程在同一个测试文件的不同片段运行，总占用保持为所选文件大小。

Direct I/O 默认开启，实际支持由 fio 和文件系统决定。若不支持会明确失败；应用不会悄悄切换为缓存测试。手动关闭 Direct I/O 后系统缓存会参与测试，应在相同缓存策略下比较结果。

SMART 不支持或无权限时显示未知/错误，不生成模拟数据。NVMe 剩余寿命根据设备的 `percentage_used` 估算，不是故障预测；ATA 厂商寿命字段没有统一定义，因此不猜测 ATA 剩余寿命。NVMe 累计主机读写量按每个数据单元 512,000 字节换算，不能等同于 NAND 实际写入量。

USB 桥接、RAID、虚拟磁盘或系统权限可能限制 SMART。勾选“管理员权限”只对 smartctl 的固定只读扫描/读取命令请求系统授权，不以管理员权限运行 fio。开发环境的磁盘枚举限制也会如实显示。

## 数据与安全设计

- 后端仅监听 `127.0.0.1` 随机端口，每次启动创建随机会话令牌；API 校验 Host、Origin 和令牌。
- 测试目标只能是可写目录，不接受原始设备或已有文件；创建私有随机临时目录，独占创建测试文件。
- 不提供格式化、擦除、TRIM、SMART 自检或修改固件功能。
- fio 调用不经过 shell；SMART 的系统授权调用会逐个引用已验证参数。
- 报告和记录在本机处理，没有遥测服务。报告可能包含目录、序列号和原始数据，分享前可自行检查。
- 设置和历史保存于 `~/Library/Application Support/SSD Lab/`。开发测试可用 `--data-dir` 或 `SSD_LAB_DATA_DIR` 指定位置。

## 源码运行与构建

Python 核心无需第三方包，要求 Python 3.10 或更新。原生窗口仅适用于 macOS；其他系统可通过本地浏览器运行核心，但本次未进行 Windows / Linux 实机验证，也不附带它们的可执行 fio / smartctl。

```sh
python3 -B app.py
python3 -B app.py --no-browser --data-dir /absolute/path/to/state
python3 -B -m unittest discover -s tests -v
```

macOS 从源码构建原生外壳需要 Swift 编译器。默认使用本机 Python；传入一个可迁移的 CPython 运行时可以生成完整应用：

```sh
python3 build_macos.py
python3 build_macos.py --python-runtime /absolute/path/to/relocatable/python
```

`tools/` 中是本次本地构建的 arm64 工具。`third-party/` 包含对应源码与许可证，可按上游说明在其他平台重新构建。使用配置如下：

```sh
# fio 3.43
MACOSX_DEPLOYMENT_TARGET=13.0 ./configure --disable-native --disable-shm
MACOSX_DEPLOYMENT_TARGET=13.0 make -j4 fio

# smartmontools 7.5
MACOSX_DEPLOYMENT_TARGET=13.0 ./configure
MACOSX_DEPLOYMENT_TARGET=13.0 make -j4 smartctl
```

本次 fio 使用上游支持的 `--disable-shm` 构建配置，以线程模式工作，避免依赖 SysV 共享内存；所有基准任务都显式设置 `--thread=1`。该配置不会跳过实际磁盘 I/O。

## 依赖与参考

- [fio 官方文档](https://fio.readthedocs.io/en/latest/fio_doc.html)；GPL-2.0-only。
- [smartmontools 官方网站](https://www.smartmontools.org/)；GPL-2.0-or-later。
- [CrystalDiskMark 官方负载配置](https://crystalmark.info/en/software/crystaldiskmark/crystaldiskmark-main-menu/)。
- 应用自己的 Python / Swift / HTML / CSS / JS 代码采用 MIT 许可，见 `LICENSE`；第三方软件遵循自己的许可。完整应用中 CPython 的许可保留在 `Contents/Resources/python/lib/python3.12/LICENSE.txt`。
