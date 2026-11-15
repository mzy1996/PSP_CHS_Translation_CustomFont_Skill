# 适配任意「走系统字库」的 PSP 游戏

本技能的核心机制只依赖两个事实：游戏用 `sceFont` 渲染文本，且文本来自 `flash0` 系统字库。
只要满足这两点，接入方式与具体作品无关。下面把「换游戏要确认什么、改什么」列清楚。

## 一、先分型：三种游戏，三条路线

| 类型 | 特征 | 路线 |
|---|---|---|
| A. 已支持外部字库 | 导入 `sceFontOpenUserFile`(0x57FCB733) 或 `sceFontOpenUserMemory`(0xBB8E7FE6) | 内置 PGF，改调用处路径参数。**不需要劫持桩** |
| B. 仅按索引取系统字库 | 只导入 `sceFontOpen`，字符串里有 `flash0:/font/*.pgf` | **需要劫持桩**，把索引打开重定向为按路径打开 |
| C. 自带字体加载器 | 有 `.pgf` 路径但没有 sceFont 符号，或字库在容器内 | 人工看引用点，通常要单独打补丁 |

判定用 `scripts/scan_font_usage.py`，结论会直接给到上表的某一行。

## 二、换游戏时需要重新确认的 6 项

1. **字库调用点**：哪些函数被导入（决定要不要桩）。
2. **系统字库索引**：`sceFontOpen(fontLib, index, ...)` 里的 `index` 对应哪份 pgf。
   日版常见 `jpn0`/`jpn1`，亚版可能用 `kr0`/`ltn0`。
3. **字号**：读游戏原字库的 `hSize/vSize`（26.6 定点，除以 64 得像素），
   或反汇编 `sceFontNewLib` 的入参 `FontNewLibParams` 里的 `hSize/vSize`。
4. **字符集**：按译文实际用字决定，简体用 GB2312，繁体用 GB2312 + Big5。
5. **字库落地路径**：必须是镜像内路径，如 `disc0:/PSP_GAME/USRDIR/xxx.pgf`。
   注意大小写：ISO9660 上一般按记录里的原始大小写匹配。
6. **桩的加载位置**：游戏从哪里加载 `libfont`，就在哪里替换。常见两类：
   - 游戏自带模块：`PSP_GAME/USRDIR/Module/libfont.prx`（或类似目录）；
   - 依赖系统模块：`flash0:/kd/libfont.prx`，需要 CFW 才能替换。

## 三、按索引→按路径的映射关系

B 类游戏的关键是「把索引翻译成路径」。劫持桩内部做的是：

```
原调用：sceFontOpen(fontLib, index, mode, err)
改后：  sceFontOpenUserFile(fontLib, "disc0:/PSP_GAME/USRDIR/<name>.pgf", mode, err)
```

因此需要注意：

- 桩只处理**一条路径**。若游戏同时打开多份字库（正文 + 标题 + 特殊符号），
  要么让这些索引都指向同一份字库（把所需字形全部收进同一 PGF），
  要么准备多个桩变体。
- `sceFontNewLib` 的 `FontNewLibParams` 里含 `numFonts`、`numFontDataEntries` 等字段，
  桩需要补齐 `openFunc/closeFunc/readFunc/seekFunc` 才能让 `sceFontOpenUserFile` 正常工作。

## 四、多语言/多版本

- **简繁双版**：字符集不同、同码位字形不同，必须分别建 PGF，不能共用。
- **日/英等其他语言保留**：把原文用字一并收进字符集，避免切语言后掉字。
- **DLC/更新档**：若 DLC 自带覆盖层 UI 数据，其文本可能不受本体补丁影响，
  需要分别回填（这属于文本侧工作，但会影响「看起来字库没生效」的判断）。

## 五、判定失败的常见原因

| 现象 | 原因 | 处理 |
|---|---|---|
| 扫描无任何线索 | EBOOT 仍是加密 `~PSP` | 先解密成 ELF 再扫 |
| 扫描只出现字体模块符号 | 扫错文件（扫到了字库模块本身） | 改扫游戏主 EBOOT / 主 PRX |
| 装了 PGF 但界面还是缺字 | 游戏走索引打开，桩没生效或路径不对 | 确认桩被加载、路径与镜像内一致 |
| 桩生效但字显示错位 | 度量口径不对（有符号/定点） | 检查编码器的 26.6 定点与符号处理 |
| 中文能显示但间距怪异 | 字号与游戏原字库不一致 | 用原字库 `hSize/64` 重生成 |

## 六、边界

- 本技能不提供解密、签名绕过或游戏分发能力。
- 只有在游戏确实走 `sceFont` 系统字库时适用；位图字体、GIM 预渲染文字要走纹理路线。