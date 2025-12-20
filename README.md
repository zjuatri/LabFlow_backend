LabFlow Backend (FastAPI)

快速启动：

1. 建议创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. 本地运行：

```powershell
# 推荐：用脚本启动（强制使用 .venv 的 Python，并限制 reload 监听范围，避免 Windows 下依赖/解释器不一致问题）
# 在仓库根目录：
powershell -ExecutionPolicy Bypass -File .\start-backend.ps1

# 或者在 LabFlow_backend 目录：
powershell -ExecutionPolicy Bypass -File .\start-backend.ps1
```

3. 健康检查：

访问 `http://localhost:8000/api/health`

Docker：

```bash
docker build -t labflow-backend .
docker run -p 8000:8000 labflow-backend
```
