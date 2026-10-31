# 首次发布检查记录

检查日期：2026-10-30（Asia/Shanghai）。本记录是首次提交准备阶段的快照，不是当前完整历史的安全结论；后续核查见 [公开前安全审计](PUBLIC_RELEASE_AUDIT.md)。本记录不包含真实数据路径、分类代码或生产统计。

## 来源与边界

- 以用户指定的 `Contour_Label_V3_4_FIX.py` 为唯一算法来源；原目录中仅有这份脚本。
- 原文件只读，未移动、覆盖或删除。
- 原文件 SHA256：`d76d454ac3318d0d158ce71efa06d86c11826f54d780b2fab6f114e9a54bd432`。
- 干净项目独立建立；没有复制原目录之外的任何生产资料。

## 脱敏结果

| 原内容类型 | 发布副本处理 |
| --- | --- |
| 4 处本机绝对路径：等高线 GDB、分幅 GDB、DEM、结果 GDB | 移到配置，改为相对路径占位示例与独立 outputs 目录 |
| 生产图层名称 | 改为明确占位值 |
| 北京 DEM/结果库名称、主说明与启动日志中的地域名称 | 改为通用名称；README 仅保留用户要求的数据隐私说明 |
| 计曲线分类代码、该代码的生产诊断结论 | 配置改为占位值，函数说明改为通用的代码/高程序列双重检查说明 |
| 注释中的示例图幅编号 | 改为 `YOUR_SHEET_ID` |
| 同事环境提示 | 改为通用 Python 3.10 / OpenFileGDB 环境提示 |

当时审查的源码与文档正文中，未发现密码、Token、API Key、内网地址、本机用户名或单位内部路径；这项结论不包含后来创建的提交作者元数据。未复制真实 GDB、DEM、Shapefile、CSV、日志、IDE 缓存。自动扫描与人工阅读不能代替权利人对代码和制图参数的授权判断。

## 代码变动

1. 将全部 79 个用户参数从主程序迁到 `config/config.example.py`。参数顺序与派生计算表达式保留；示例路径、图层和代码使用占位值。
2. 新增 `load_config`、`preflight_config`、`cli`。导入模块只加载示例参数，不读写 GIS 数据；直接运行时默认要求完整的 `config/config.local.py`，也可用 `--config` 指定可信配置。相对数据路径以进程工作目录为基准。
3. 新增读写前检查：拒绝占位值；输出必须为独立 `.gdb` 路径，不能与输入相同、互为父子目录。此检查是路径保护，不是任意数据库内容审查。
4. 示例 `OVERWRITE_OUTPUT_GDB` 从原默认 `True` 改为 `False`。用户显式启用后仍沿用原来的整库删除与写入逻辑。
5. 调整两处运行提示及文档说明。`__main__` 入口从调用 `main()` 改为调用 `cli()`；保留异常输出和退出码处理。

对原程序顶层全部 **41 个函数/类** 进行 AST 对比：忽略文档字符串，并仅归一化上述两处提示文本后，执行结构全部相同。计曲线识别、端点连接、候选生成、图框控制、高侧推断、DEM 分支、文字方向、覆盖与去密、reject 和文件读写函数均保留。AST 检查不是完整生产输出一致性证明。

逐项求值核对 79 个配置参数：71 项与原值相同，仅 4 个数据路径、2 个图层名称、计曲线代码和输出覆盖开关这 8 项按上述发布要求改变。

保留兼容性代码：GeoPandas `union_all()` 不可用时回退 `unary_union`，Pyogrio 写入使用 `use_arrow=False`。没有加入需要新版 GeoPandas 的专用 API。

原有未参与流程读取的参数 `MIN_LOGICAL_LINE_LENGTH_M`、`REQUIRE_INDEX_ELEVATION_COVERAGE`、`PREFER_LARGER_FRAME_CLEARANCE` 继续保留；`PREFER_NORTHWARD_TEXT_TOP` 仅用于汇总。没有借整理项目之机修改这些历史行为。

## 验证环境与结果

为避免修改系统 Python，在项目仓库之外建立隔离测试工具目录，运行时由 Python 官方发行包提供，依赖从包索引安装。没有把测试工具、运行时或依赖目录放入发布仓库。

| 项目 | 已验证值 |
| --- | --- |
| 系统 / Python | Windows / CPython 3.10.11 |
| GeoPandas | 0.14.4 |
| Pyogrio / GDAL | 0.12.1 / 3.11.4 |
| Rasterio | 1.4.4 |
| Shapely | 2.1.2 |
| PyProj | 3.7.1 |
| Pandas / NumPy | 2.3.3 / 2.2.6 |
| tqdm | 4.70.1 |
| OpenFileGDB | `rw` |

- Python 3.10 语法编译检查通过。
- `python -m unittest discover -s tests -v`：**36 项全部通过，无跳过**。
- 其中 32 项测试覆盖内存合成几何、配置加载与保护；4 项在系统临时目录创建合成 FileGDB，验证 `index_only` / `all` / `both` 以及合成 DEM 的完整输入输出流程。
- 合成端到端测试核对正式点高程、方向、高侧、分类和图幅字段，以及 index/reject/audit 图层和三个 CSV 的生成。
- 合成 DEM 测试触发依赖库内部 Affine 矩阵乘法的待弃用提示；测试通过，未为消除上游提示修改原算法。
- 测试不使用真实北京数据，也不包含 ArcMap 10.2.2 界面标注验收。其他 GDAL 驱动组合、其他平台与真实生产结果仍需用户验证。

## Git 与上传边界

- 默认忽略本地配置、生产数据、GIS 辅助文件、CSV、日志、缓存、虚拟环境以及 data/outputs/results 等目录。
- 使用显式源码文件清单暂存，提交前检查暂存文件、差异与敏感信息。
- 暂存区共 10 个源码/文档文件；敏感模式扫描、Python 3.10 AST 解析、`git diff --cached --check` 均通过。另验证 23 个数据/缓存/私有配置路径被忽略，10 个发布文件均不会被误忽略。
- 首条提交信息：`Initial release of contour label placement workflow`。
- 首次远程仓库计划：`contour-label-placement`，**Private**。
- 准备阶段未找到 GitHub CLI；当时尚未创建远程仓库或 push。
- 2026-10-31：用户创建 Private 远程仓库并完成 Git Credential Manager 授权，首条提交已推送。提交作者姓名与个人邮箱记录在旧提交历史中。
- 后续新提交经用户确认改用核实的 GitHub noreply 邮箱；没有改写旧提交，也没有改变仓库可见性。
- 未添加许可证；未来公开或授权他人使用前需权利人决定。
