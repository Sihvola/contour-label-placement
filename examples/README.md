# 示例输入说明

本仓库不分发真实 GIS 数据。`tests/test_core.py` 在内存中构造直线、闭合线、相邻不同高程线和矩形图幅，不依赖真实 GDB 或 DEM。

## README 示意图

从项目根目录运行：

```powershell
python examples/make_synthetic_preview.py
```

脚本仅使用项目现有依赖，在内存构造 5 条合成等高线和一个矩形图幅，调用稳定版核心函数，生成 `docs/images/candidates-to-labels.svg` 和 `docs/images/label-orientation.svg`。得到 450 个几何合格候选点、5 个最终注记点与 445 条质检记录；未读取真实文件或 DEM，未改变算法或默认参数。SVG 不含脚本、外部图片或字体引用。

这是邻近等高线证据、方向与去密流程的可复现示意，不替代完整 FileGDB 流程和 ArcMap 制图验收。不要把真实地图截图替换进这些示例而未经脱敏审查。

## 自备输入

运行完整流程前，请自行准备：

1. 等高线 FileGDB：线几何、米制投影坐标系，含可转为数值的高程字段和分类代码字段（默认 `ELEV`、`OBJE_CODE`）。
2. 1:10000 标准分幅 FileGDB：面几何、明确的坐标系、图幅编号字段（默认 `TFBH`）。
3. 可选 DEM：带 CRS 和有效高程值的栅格；高程单位与等高线一致。无 DEM 时设置 `USE_DEM_FALLBACK = False`。

建议将自备数据放在项目下的 `data/`，输出放在 `outputs/`。这些目录默认被 Git 忽略。复制 `config/config.example.py` 为 `config/config.local.py`，修改路径、图层名、字段名和计曲线代码后运行。

示例中的 `REPLACE_WITH_*` 不是实际图层或分类代码，必须替换。计曲线代码应从自己的数据字典核实，不要从其他项目照搬。

首次验证可用 `ONLY_TFBH = ["YOUR_SHEET_ID"]` 限定图幅；程序保留分析缓冲范围内完整的源等高线，不沿图框裁断高侧判断证据。人工复核正式点、reject、逐线审计以及高程覆盖 CSV 后，再扩大范围。
