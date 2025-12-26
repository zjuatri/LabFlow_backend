# PDF 表格公式视觉识别（Route A）

接口：`POST /api/projects/{project_id}/pdf/table/formula/vision`

用途：
- 只针对“表格内公式”场景。
- 不需要手动框选。
- 后端会用 `pdfplumber` 找表格单元格 bbox，然后对每个单元格做裁剪渲染（PNG）并调用 GLM Vision，拿到 `latex` 后回填到对应 cell。

重要限制：
- 如果 PDF 的表格是“扫描图片/截图”（页面内容本质是位图），`pdfplumber` 往往无法抽取出结构化表格（`find_tables`/`extract_tables` 都会是 0）。
  这时就拿不到单元格 bbox，自然也无法渲染单元格裁剪图，因此接口会返回空的 `rendered_cell_images`。
  这种 PDF 需要走“整页视觉理解 + OCR/表格重建”的路线（更重，后续可加）。

## 请求

- `Content-Type: multipart/form-data`
- 表单字段：`file`（PDF 文件）
- Query（可选）：
  - `page_start`: 从第几页开始（1-based）
  - `page_end`: 到第几页结束（1-based）
  - `max_pages`: 最多处理多少页（默认 2）
  - `render_scale`: 渲染倍率（默认 2.0）
  - `model`: GLM 视觉模型（默认 `glm-4.6v-flash`）

## 环境变量

- `GLM_API_KEY`: 智谱 BigModel API Key

## 返回

- `tables[]`: 每一项包含页号与表格索引，以及该表格内所有单元格（按 bbox 去重后的列表）
  - `cells[]`: `{ content, bbox, latex }`
- `rendered_cell_images[]`: 每个单元格裁剪渲染后的图片（已保存到 `/static/projects/{id}/images/...`）
- `diagnostics`: 诊断信息（用于排查为什么没抽到表格/没渲染出单元格图片）

> 说明：为了先把“回填到正确位置”的链路打通，这个接口当前返回的是“以 bbox 为主键的 cell 列表”。
> 下一步如果你希望保持 ingest 的二维 `cells[row][col]` 结构，我们可以把它合并回 `/pdf/ingest` 的 `tablePayload.cells`。
