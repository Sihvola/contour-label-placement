# v0.1.0

首次稳定版本：用于 1:10000 地形图的等高线高程注记点生成与方向计算流程，兼容 Python 3.10。

## 内容

- 保留稳定 V3.4 核心算法：计曲线识别、相邻等高线高侧判断、可选 DEM 检查与回退、图框和端点避让、平直段筛选、字头方向、去密与 reject 质检。
- 独立占位配置、中文使用文档、明确的 GIS 数据和缓存忽略规则。
- 36 项通过的单元与合成 FileGDB 流程测试；没有使用真实生产数据。
- 两张可复现 SVG：450 个合成候选点到 5 个最终注记点，以及选中注记的高侧与方向属性。
- 完整 Git 历史和当前文件的公开前安全审计；本次文档和示例更新不修改核心程序或配置参数。

## 已验证环境

Windows / Python 3.10.11；GeoPandas 0.14.4、Pyogrio 0.12.1、Rasterio 1.4.4、Shapely 2.1.2。FileGDB 写入需本机 GDAL 的 OpenFileGDB 驱动支持。

## 公开状态与已知事项

本 Release 位于 **Private 仓库**，没有改变仓库可见性。未发现已提交的凭据或真实生产数据，但旧提交作者/提交者元数据含个人联系邮箱。新提交改用 GitHub noreply 并不清除旧历史，公开前仍需所有者决定如何处理。当前尚未添加开源许可证。

示意图不是 ArcMap 截图，也不是实际生产地形或生产验收结果。运行需要用户自行准备输入，且只能加载可信的 Python 配置。输出覆盖默认关闭。

- [使用说明与示意图](https://github.com/Sihvola/contour-label-placement/blob/v0.1.0/README.md)
- [公开前安全审计](https://github.com/Sihvola/contour-label-placement/blob/v0.1.0/docs/PUBLIC_RELEASE_AUDIT.md)
