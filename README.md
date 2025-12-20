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
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

3. 健康检查：

访问 `http://localhost:8000/api/health`

Docker：

```bash
docker build -t labflow-backend .
docker run -p 8000:8000 labflow-backend
```
