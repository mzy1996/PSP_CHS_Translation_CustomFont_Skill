---
name: psp-chs-custom-font
description: 让 PSP 游戏显示任意中文字形而摆脱系统日文字库缺字限制，适用于任何走 sceFont 系统字库的 PSP 游戏（不限于某一款作品）。覆盖字库使用方式判定、TTF 转 PSP PGF 字库、PGF 编解码与往返校验、libfont 劫持桩路径改写、字库内置与镜像回注。当需要给 PSP 游戏换中文字库、解决掉字（繁体/简体缺字）、判断某个游戏是否需要劫持桩、或把自制 PGF 塞进镜像时使用本技能。
---

# PSP 自制中文字库（PGF 生成与接入）

## 这个技能解决什么

PSP 上大量游戏用系统字库渲染文本：

```
sceFontNewLib(...) → sceFontOpen(fontLib, index, mode, err) → 字形页 → 文本
```

`sceFontOpen` 是**按索引**取 `flash0:/font/*.pgf`（`jpn0.pgf` 等）。日文字库只有 JIS 字形，
汉化后的简体/繁体必然缺字，表现是「本遊戲支援自動存_功能」这类掉字。

本技能把「按索引取系统字库」换成「按路径读镜像内自带 PGF」：

1. 判定目标游戏**怎么取字库**（决定要不要劫持，见第 0 步）；
2. 用任意 TTF 生成覆盖实际用字的 PGF；
3. 若游戏只支持按索引打开，插入 `libfont` 劫持桩把调用重定向为按路径打开；
4. 字库随镜像分发，不依赖 flash0，也不受 CFW 字体替换影响。

## 适用与不适用

适用——只要游戏**走 sceFont 系统字库**即可，与具体作品无关：

- 任何 PSP 游戏汉化后需要显示简体/繁体中文；
- 想把游戏内字体统一成某一款 TTF（同字号、同字重）；
- 想摆脱「只能用日文字库里已有汉字」这种妥协；
- 需要判断某游戏到底用 `sceFontOpen` 还是已经支持 `sceFontOpenUserFile`。

不适用：

- 只改文本、不改字库的纯文本回填；
- 非 sceFont 渲染的预渲染贴图文字（要改纹理）；
- 加密/签名绕过、游戏分发。

## 第 0 步：判定目标游戏的字库使用方式（先做这一步）

不同游戏中字库接入点完全不同，**不要跳过判定直接套劫持桩**。

```bash
python scripts/scan_font_usage.py /path/to/EBOOT.BIN
python scripts/scan_font_usage.py /path/to/EBOOT.BIN --pgf /path/to/jpn0.pgf
```

| 扫描结果 | 判定 | 路线 |
|---|---|---|
| 含 `sceFontOpenUserFile` / `sceFontOpenUserMemory` | 游戏本身支持按路径或内存打开字体 | **不需要劫持桩**：内置 PGF，改调用处路径参数即可 |
| 仅有 `sceFontOpen` | 只按索引取系统字库 | **需要劫持桩**，走第 3 步 |
| 有 `flash0:/font/*.pgf` 路径串 | 取字库走系统 | 大概率需要劫持桩 |
| 只有 `.pgf` 串、无 sceFont 符号 | 可能自带专用加载器 | 人工看引用点，不套用劫持 |
| 无任何线索 | 字库可能在容器内，或是内嵌位图字体 | 先解容器 / 换思路 |

注意：

- 加密的 `~PSP` EBOOT 静态扫描无效，必须先解密成 ELF；解密不属于本技能范围。
- 扫到的是**字体模块自身**（如导出 `sceLibFont` 的 prx）时不能作为判据，要改扫游戏主模块。

## 关键事实

- **PGF 不是 TTF 打包**。它是 Sony 私有格式：392 字节头 + 位打包索引表 +
  nibble RLE 压缩的字形位图。必须按格式写，缺一段 `sceFontOpen` 就会失败或空白。
- **只导入 `sceFontOpen`** 的游戏，光在镜像里塞 PGF 没用，必须配合改过的 `libfont.prx`。
- **PPSSPP 不加载镜像内的 prx**，它用 HLE 字库并读取 `assets/flash0/font/`。
  所以模拟器验证换字库要替换 `assets/flash0/font/jpn0.pgf`，真机才用劫持桩。
- PGF 里的度量是 **26.6 定点**：`xAdjustH = left<<6`、`yAdjustH = top<<6`、
  `advanceH = 字宽<<6`。

## 工作流

### 第 1 步：确定字符集

从游戏文本资源里提取实际用字，再补底：

- 简体版：GB2312 全集 + 游戏文本 + ASCII + 标点 + 假名
- 繁体版：GB2312 + Big5 全集 + 游戏文本 + ASCII + 标点 + 假名

只覆盖实际用字能让字库明显变小（实测繁中 16629 字形 3.0MB、简中 8302 字形 1.5MB）。
用 `--charset cht|chs` 控制，`--text` 传游戏文本（JSONL，每行含 `text` 字段）。

### 第 2 步：TTF → PGF

```bash
python scripts/make_pgf.py \
    --ttf /path/to/font.ttf \
    --size 18 \
    --charset cht \
    --text /path/to/text.jsonl \
    --out build/myfont_cht.pgf
```

字号要与游戏原字库一致，否则中文会比原文偏大／偏小。取字号两种方法：

- `python scripts/pgf.py /path/to/jpn0.pgf` 读 `hSize/vSize`（26.6 定点，除以 64 得像素）；
- 或 `scan_font_usage.py --pgf <某个 pgf>` 直接打印字号。

生成后自检：重新加载 PGF，抽样字符检查字形存在且位图非空；探针自动取该游戏文本用字，
换游戏时不必改脚本。

### 第 3 步：（仅当需要）改劫持桩路径

前提：手上已有一个可用的 `libfont` 劫持桩——它自身导出与系统同名的字体库模块，
把 `sceFontOpen` 重定向为 `sceFontOpenUserFile`。桩通常来自其他汉化项目，**仓库不附带**。

```bash
python scripts/patch_libfont_path.py libfont.prx \
    -n "disc0:/PSP_GAME/USRDIR/myfont_cht.pgf" \
    -o build/libfont_patched.prx
```

脚本会：定位原 `.pgf` 路径串（可用 `-old` 指定）→ 在 `.rodata` 尾部空隙写入新串
（长度可不同）→ 改写引用旧地址的 `addiu` 立即数 → 同步扩展 `.rodata` 节大小 → 校验 ELF 结构。

桩内路径**必须 `disc0:` 开头并指向镜像内实际位置**。

### 第 4 步：写回镜像

字库是新文件时追加到目录区：

```bash
python scripts/iso_add_file.py /path/to/game.iso /path/to/patched.iso \
    build/myfont_cht.pgf=myfont_cht.pgf
```

桩要放在游戏实际加载 `libfont` 的位置（各作不同：`USRDIR/Module/libfont.prx`、
`flash0:/kd/libfont.prx` 等）。`iso_add_file.py` 只做**新增**；目标位置已有同名文件时，
要按 ISO9660 目录项替换原 extent。

若字库或桩位于 BARC 容器内，做成员级替换：

```bash
python scripts/barc_repack.py \
    --iso /path/to/game.iso \
    --arc-rel PSP_GAME/USRDIR/<容器目录>/<容器>.arc \
    --member <成员相对路径> \
    --new-plain /path/to/new.bin \
    --out-iso /path/to/patched.iso
```

### 第 5 步：验证

- 字库结构：`python scripts/pgf.py build/myfont_cht.pgf`
- 编解码往返：`python scripts/pgfenc.py build/myfont_cht.pgf 400`
- PPSSPP：把 PGF 命名为游戏实际使用的系统字库名（日版常见 `jpn0.pgf`），
  替换 `assets/flash0/font/jpn0.pgf`。模拟器走 HLE、不加载镜像内 prx，只能验字库本身。
- 真机：装改好的镜像与桩，逐界面检查中文是否完整、有无缺字或错位。

## 脚本清单

| 脚本 | 作用 |
|---|---|
| `scripts/scan_font_usage.py` | 判定游戏的字库使用方式，给出「是否需要劫持桩」 |
| `scripts/pgf.py` | PGF 解码/编码核心（头、位打包表、nibble RLE 位图） |
| `scripts/pgfenc.py` | 编码器与往返校验 |
| `scripts/make_pgf.py` | TTF 渲染 → 生成 PGF（字符集选择、自检） |
| `scripts/patch_libfont_path.py` | 改写劫持桩内的字库路径 |
| `scripts/barc_repack.py` | BARC 成员级原地替换（容器总长不变） |
| `scripts/iso_add_file.py` | 向 ISO 目录新增文件 |
| `scripts/dis_libfont.py` | 反汇编 PRX 指定段并标注 `.rel` 重定位 |

## 可调参数一览（换游戏时主要改这些）

| 参数 | 说明 | 怎么定 |
|---|---|---|
| 字号 `--size` | 字库像素高度 | 取游戏原字库 `hSize/64` |
| 字符集 `--charset` | `cht` 含 Big5 全集、`chs` 仅 GB2312+游戏文本 | 按译文用字选 |
| 字体 `--ttf` | 任意 TTF/OTF | 按风格选，注意授权 |
| 字库名/路径 | 桩里写死的那条 `disc0:` 串 | 与镜像内实际位置一致 |
| 桩位置 | 游戏实际加载 `libfont` 的目录 | 各作不同，需实测 |

## 常见坑

1. **度量符号**：PGF 里 32 位度量是**有符号**，按无符号读会让字形位置错乱。
2. **末尾填充**：解码端要求 `charPtr+1024` 位仍在数据内，最后一个字形靠近末尾会被丢，
   编码时补 512 字节 0。
3. **charPointerBpe 溢出**：偏移单位是 32bit，字库超过约 2MB 时 19bit 装不下，要用 24bit。
4. **`・`(U+30FB) 不是假名**：做「译净校验」时别把它算进 `\u3040-\u30ff`，
   否则含中点的词条会被整批误杀。
5. **BARC 整体重压缩会破坏布局**：只替换目标成员，其余字节与容器总长不动，
   否则游戏可能直接退出。
6. **模拟器与真机路径不同**：PPSSPP 走 `assets/flash0/font`；真机走桩或 flash0 替换，
   两者要分别验证。
7. **不要跳过第 0 步**：对已支持 `sceFontOpenUserFile` 的游戏硬塞劫持桩，
   会多一层不必要的风险。

详见 `references/pgf-format.md`、`references/measured-params.md`、
`references/hijack-mechanism.md` 与 `references/game-adaptation.md`。