# libfont 劫持桩机制

PSP 的 `sceFont` 接口默认按索引打开系统字库（`flash0:/font/jpn0.pgf` 等）。
汉化字库要跟镜像走，就需要把「按索引打开」改成「按路径打开镜像内自带的 PGF」。
在游戏只导入 `sceFontOpen` 的情况下，这一步由一个自制的 `libfont.prx` 劫持桩完成。

## 1. 样本

| 项目 | 值 |
|---|---|
| 文件 | `libfont.prx` |
| 大小 | 6666 字节 |
| 模块名 | `sceLibFttt` |
| 特征字符串 | `fontfuck`、`Fontfuck Start!` |

样本是明文 ELF32 little-endian PRX，节表完整，可直接用 `scripts/dis_libfont.py`
反汇编和定位字符串。

## 2. 原理

1. 桩自身导出与系统模块同名的 `sceLibFttt`，在系统模块解析时顶替原模块。
2. 取得 FontLibrary 结构后改写其中的函数指针，使游戏原本的调用进入桩代码。
3. 把游戏的

   ```c
   sceFontOpen(fontLib, index, mode, err)
   ```

   重定向为

   ```c
   sceFontOpenUserFile(fontLib, "disc0:/PSP_GAME/USRDIR/<name>.pgf", mode, err)
   ```

   这样字库来源就从系统 flash0 变成了镜像内的 PGF。
4. 在 `sceFontNewLib` 中补齐 `openFunc`、`closeFunc`、`readFunc`、`seekFunc`
   四个文件操作回调，保证 `sceFontOpenUserFile` 能读到 `disc0:` 上的文件。

## 3. 原始字符串与地址

`.rodata` 节：文件偏移 `0xcf0`，虚拟地址 `0xc90`，原始大小 `0x163`。

| 字符串 | 文件偏移 | 虚拟地址 | 说明 |
|---|---:|---:|---|
| `disc0:/PSP_GAME/USRDIR/oldfont.prx` | 0xd10 | 0xcb0 | 桩内引用的旧 prx 路径 |
| `disc0:/PSP_GAME/USRDIR/fonts.pgf` | 0xd34 | 0xcd4 | 实际被 `addiu` 引用的字库路径，默认改这一条 |
| `<NUL>` | 0xd58 | 0xcf8 | 字面量占位串，被 vaddr `0x0844` 的 `addiu $s3,$s3,0xcf8` 引用，**不可覆盖** |
| `Fontfuck Start!` | 0xcfc | 0xc9c | 桩启动提示 |

机器码层面，引用 `fonts.pgf` 的指令在 `.text` 内：

```text
vaddr 0x010c（文件偏移 0x16c）：24 a5 0c d4   addiu $a1, $a1, 0xcd4
```

`0xcd4` 正是 `fonts.pgf` 的虚拟地址。

## 4. 改造方法（手工样本）

目标路径（样本，换游戏时替换为你的字库名与镜像内实际路径）：
`disc0:/PSP_GAME/USRDIR/<yourfont>.pgf`（37 字符 + NUL，共 38 字节）。

1. **写新路径**：写入文件偏移 `0xe58`，对应虚拟地址 `0xdf8`。
   该位置在 `.rodata` 原结尾（`0xcf0 + 0x163 = 0xe53`）之后的空区，不覆盖任何已有数据。
2. **改引用立即数**：把 vaddr `0x010c`（文件 `0x16c`）的
   `addiu $a1,$a1,0xcd4` 改为 `addiu $a1,$a1,0xdf8`：

   ```text
   0x24a50cd4  ->  0x24a50df8
   ```

3. **扩展节大小**：`.rodata` 的 `sh_size` 从 `0x163` 改为 `0x18e`
   （`0xdf8 + 38 - 0xc90 = 0x18e`），让新串落在节覆盖范围内。
4. 校验 ELF 节表、程序头覆盖范围与新串可读性。

仓库脚本 `scripts/patch_libfont_path.py` 自动完成上述步骤，但空位选择策略不同：
它从 `.rodata` 尾部第一个 0 字节开始写（本样本为文件 `0xe53` / 虚拟地址 `0xdf3`），
因此实际偏移与 `.rodata` 新大小可能和上面的手工数值略有差异（实测 `0x189`）。
两者效果等价，交付时以脚本打印的地址为准。

## 5. 使用前提判据

先解析游戏 EBOOT 的导入表：

- 若已导入 `sceFontOpenUserFile`（NID `0x57FCB733`）或
  `sceFontOpenUserMemory`（NID `0xBB8E7FE6`），游戏本身就能按路径打开字库，
  **不需要劫持桩**，直接在镜像内放入 PGF 并让游戏引用即可。
- 若只导入 `sceFontOpen`，则必须使用劫持桩，否则镜像里放多少 PGF 都不会被读取。

## 6. 模拟器与真机的差异

- **PPSSPP 不加载镜像内的 prx**。它用 HLE 实现 `sceFont`，并读取
  `assets/flash0/font/` 下的系统字库。因此在 PPSSPP 里验证换字库，要替换
  `assets/flash0/font/jpn0.pgf`（或对应语言字库），劫持桩不会生效。
- **真机（或加载 prx 的 CFW 环境）才走劫持桩**。桩路径、模块加载顺序、
  `disc0:` 映射都必须在真机上验证。
- 两套验证要分开做：PPSSPP 通过只能说明 PGF 本身可解码，不能说明桩可用；
  真机通过才说明字库接入链路完整。