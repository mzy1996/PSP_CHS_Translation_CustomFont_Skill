# PSP 自制中文字库：PGF 生成与 libfont 劫持

让 **任何走 PSP 系统字库（`sceFont`）的游戏**显示中文字形，而不受系统日文字库缺字限制。

PSP 的 `sceFontOpen(fontLib, index, mode, err)` 按**索引**打开系统字库
（`flash0:/font/jpn0.pgf` 等），日文字库只有 JIS 字形，汉化文本必然出现掉字。
本仓库解决的核心问题是：把「按索引取系统字库」改为「按路径读镜像内自带的 PGF」，
字库随镜像分发，不依赖 flash0，也不受 CFW 字体替换影响。

这条链路与具体作品无关：判定游戏如何取字库 → 用 TTF 生成覆盖实际用字的 PGF →
（必要时）改掉劫持桩里的字库路径 → 把字库与桩写回镜像。

## 能力范围

- 判定目标游戏的字库使用方式：走 `sceFontOpen` 还是已支持 `sceFontOpenUserFile`，
  据此给出「是否需要劫持桩」的结论；
- 把 TTF/OTF 渲染为 PSP `sceFont` 可用的 PGF v2 字库；
- 选择简体（GB2312）或繁体（GB2312 + Big5）字符集，并合并游戏实际文本用字；
- 解码、检查、重新编码 PGF，做字形位图与度量字段的往返比对；
- 反汇编明文 PRX，查看劫持桩代码与重定位；
- 改写劫持桩内的字库路径，并同步扩展 `.rodata`；
- 向 ISO9660 镜像新增文件；
- 对 BARC 容器做成员级原地替换（不重建容器）。

不适用：

- 只改文本、不改字库的纯文本回填；
- 非 `sceFont` 渲染的预渲染贴图文字（要改纹理）；
- 加密/签名绕过、ISO 制作与游戏分发。

## 目录结构

```text
.
├── SKILL.md                     技能说明：适用场景、判定步骤、工作流与关键事实
├── README.md                    本文件
├── scripts/
│   ├── scan_font_usage.py       判定游戏字库使用方式与建议路线
│   ├── pgf.py                   PGF 解码/编码核心（头、位打包表、nibble RLE 位图）
│   ├── pgfenc.py                PGF 编码器与往返验证
│   ├── make_pgf.py              TTF 渲染 → 生成 PGF（字符集选择、自检）
│   ├── patch_libfont_path.py    改写劫持桩内的字库路径
│   ├── barc_repack.py           BARC 成员级原地替换（容器总长不变）
│   ├── iso_add_file.py          向 ISO 目录新增文件
│   └── dis_libfont.py           反汇编 PRX 指定段并标注 .rel 重定位
├── references/
│   ├── pgf-format.md            PGF 文件格式与字段说明
│   ├── measured-params.md       一组完整跑通的实测参数与体积对照
│   ├── hijack-mechanism.md      劫持桩原理、字符串/地址与改造方法
│   ├── game-adaptation.md       换游戏时的判定清单与常见失败原因
│   └── .gitkeep                 占位
└── examples/
    └── .gitkeep                 占位（不附带游戏资源）
```

## 快速上手

命令都在仓库根目录执行，路径按实际环境替换。

### 0. 判定游戏怎么取字库（先做这一步）

```bash
python scripts/scan_font_usage.py /path/to/EBOOT.BIN
```

输出会落在三种结论之一：已支持外部字库（**不需要**劫持桩）、只按索引取系统字库
（**需要**劫持桩）、或需要人工确认。加密的 `~PSP` 模块要先解密成 ELF 再扫。
判定细则与失败原因见 `references/game-adaptation.md`。

### 1. 准备字符集

从游戏文本资源导出用字，整理成 JSONL：每行一个对象，至少含 `text` 字段，例如
`{"text": "..."}`。没有文本清单时，`--charset` 只能覆盖 GB2312/Big5 全集、ASCII、
常用标点与假名，可能缺少游戏自造词用字。

### 2. 生成 PGF

```bash
python scripts/make_pgf.py \
    --ttf /path/to/font.ttf \
    --size 18 \
    --charset cht \
    --text /path/to/text.jsonl \
    --out build/myfont_cht.pgf
```

简体版把 `--charset` 改为 `chs`。字号要与该游戏原字库一致：读 `jpn0.pgf` 一类的
`hSize/vSize`（26.6 定点，除以 64 得像素），或直接
`python scripts/scan_font_usage.py /path/to/EBOOT.BIN --pgf /path/to/jpn0.pgf`。
生成后脚本会重新加载 PGF，抽样检查字形是否存在、位图是否非空；
探针字符自动取自 `--text` 的文本，换游戏不必改脚本。

### 3. 改写劫持桩路径（仅 B 类游戏需要）

前提：已有一个可用的 `libfont` 劫持桩（自身导出同名字体库模块，把 `sceFontOpen`
重定向为 `sceFontOpenUserFile`）。桩通常来自其他汉化项目，**本仓库不附带**。

```bash
python scripts/patch_libfont_path.py /path/to/libfont.prx \
    -n "disc0:/PSP_GAME/USRDIR/myfont_cht.pgf" \
    -o build/libfont_patched.prx
```

脚本自动定位原 `.pgf` 路径串，在 `.rodata` 尾部空位写入新串，改写引用旧地址的
`addiu` 立即数，并扩展 `.rodata` 节大小。旧串不是默认那一条时用 `-old` 指定。

### 4. 写回镜像

字库是新文件时追加到目录区：

```bash
python scripts/iso_add_file.py /path/to/game.iso /path/to/patched.iso \
    build/myfont_cht.pgf=myfont_cht.pgf
```

桩要放到游戏实际加载 `libfont` 的位置（各作不同）。`iso_add_file.py` 只做**新增**；
目标位置已有同名文件（例如 `USRDIR/Module/libfont.prx`）时，要按 ISO9660 目录项
替换原 extent，而不是新增。

若目标资源是 BARC 容器内的成员，用成员级替换：

```bash
python scripts/barc_repack.py \
    --iso /path/to/game.iso \
    --arc-rel PSP_GAME/USRDIR/<容器目录>/<容器>.arc \
    --member <成员相对路径> \
    --new-plain /path/to/new.bin \
    --out-iso /path/to/patched.iso
```

脚本会重新解析输出镜像并解压目标成员，与 `--new-plain` 逐字节比对，成功时打印
`OK bytes=N`。

### 验证

- 字库结构：`python scripts/pgf.py build/myfont_cht.pgf`
- 编解码往返：`python scripts/pgfenc.py build/myfont_cht.pgf 400`
- PPSSPP：把生成的 PGF 命名为游戏实际使用的系统字库名（日版常见 `jpn0.pgf`），
  替换 `assets/flash0/font/jpn0.pgf`；模拟器不加载镜像内 prx，只能验证字库本身。
- 真机：装改好的镜像与桩，逐界面检查中文是否完整显示。

## 脚本参数

| 脚本 | 主要参数 | 用途 |
|---|---|---|
| `scan_font_usage.py` | `<ELF/PRX>` `[--pgf <pgf>]` | 判定字库使用方式，给出是否需要劫持桩 |
| `pgf.py` | `<file.pgf>` | 解析 PGF 头、表与字形，打印结构并抽样解码 |
| `pgfenc.py` | `<file.pgf> [limit]` | 重新编码后逐字形比对位图与度量 |
| `make_pgf.py` | `--ttf` `--size` `--charset cht\|chs` `--text` `--out` `[--font-name]` | TTF 渲染生成 PGF，含自检 |
| `patch_libfont_path.py` | `<libfont.prx>` `-n/--new <path>` `-o/--out <prx>` `[-old <path>]` | 改写桩内字库路径、引用立即数与 `.rodata` 大小 |
| `barc_repack.py` | `--iso` `--arc-rel` `--member` `--new-plain` `[--out-iso]` | BARC 成员级原地替换，容器总长与其它成员偏移不变 |
| `iso_add_file.py` | `<src.iso> <dst.iso> <local=isoname>...` | 向目录新增文件，追加数据并更新 PVD |
| `dis_libfont.py` | `--prx` `--out` `[--section .text]` | 反汇编指定节，行尾标注 `.rel` 重定位 |

各脚本都支持 `--help`。`barc_repack.py` 不传 `--out-iso` 时就地写回输入镜像；
`iso_add_file.py` 首次运行会把源镜像复制到目标路径再追加。

## 实测案例

在 PSP《高达：战争记忆》简繁双版汉化中，用文泉驿微米黑 18 px 生成两套字库：

| 版本 | 文件 | 字形数 | 文件大小 | 字符集 |
|---|---|---:|---:|---|
| 繁体 | `myfont_cht.pgf` | 16629 | 3,040,804 字节 | GB2312 + Big5 + 游戏文本 + ASCII + 标点 + 假名 |
| 简体 | `myfont_chs.pgf` | 8302 | 1,525,972 字节 | GB2312 + 游戏文本 + ASCII + 标点 + 假名 |

两套字库的 PGF 头均为 revision=2、version=6、bpp=4、`hSize=vSize=1152`、
分辨率 8192、`headerSize=0x188`，`charMapBpe=16`、`charPointerBpe=24`。
一轮全量往返校验（9777 字形样本）中，位图逐像素与全部度量字段零差异。

这组数值只作为参照：换游戏时字号与字符集都要按该游戏重新确定。
完整参数见 `references/measured-params.md`，劫持桩细节见
`references/hijack-mechanism.md`，换游戏要确认的项见
`references/game-adaptation.md`。

## 关键坑

1. **PGF 不是 TTF 打包**。它是 Sony 私有格式：392 字节头 + 位打包索引表 +
   nibble RLE 字形位图。必须按格式写，缺一段都会导致 `sceFontOpen` 失败或空白。
2. **fontData 末尾要补 512 字节 0**。解码端有 `charPtr+1024 > fontDataSize*8`
   的越界检查，否则最后一个字形会被丢弃。
3. **32 位显式度量按有符号解释**。按无符号读会把负值读成约 42 亿，字形位置错乱。
4. **BARC 只能做成员级原地替换**。新压缩流不长于原流时才就地覆盖并只改
   `data_size`；重排成员或重建容器会让游戏启动即退出。
5. **PPSSPP 和真机走不同路径**。PPSSPP 用 HLE 与 `assets/flash0/font/`，
   不加载镜像内 prx；真机才走劫持桩。两边要分别验证。
6. **繁体与简体要分别建字库**。Big5 与 GB2312 的字符集和同码位字形都不同，
   不能共用一份 PGF。
7. **不要跳过判定**。对已支持 `sceFontOpenUserFile` 的游戏硬塞劫持桩，
   会多一层不必要的风险。

## 限制与合规

- 仓库只提供脚本、格式说明与实测参数，**不分发游戏 ISO、EBOOT、PRX 或字库二进制**，
  也不附带劫持桩本体。
- 请只对自己合法拥有的游戏副本操作，并自行确认当地法律与游戏许可条款。
- `examples/` 为占位目录，不含可直接运行的样本；所有路径均为占位符，需按实际环境替换。
- 脚本不提供加密、签名或版权保护绕过能力。

## 许可与致谢

仓库当前未附独立的 LICENSE 文件；脚本与文档的使用、修改与再分发，请遵循仓库托管页面
的说明，或先与作者确认。

PGF 格式实现参考了 [PPSSPP](https://www.ppsspp.org/) 项目的
`Core/Font/PGF.h`、`PGF.cpp` 及其 HLE 字体实现，在此致谢。示例中的文泉驿微米黑由其
作者发布，使用与再分发请遵守字体附带的许可。