# 悟理 MCP 服务镜像
# 部署：docker build -f deploy/mcp-server/Dockerfile -t wuli-mcp .
# 运行（公网投稿用）：docker run -p 8000:8000 -v /path/to/student-error-library:/data/library \
#   -e WULI_LIBRARY=/data/library wuli-mcp
FROM python:3.11-slim

WORKDIR /app

COPY deploy/mcp-server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 需要完整的仓库布局：mcp_server 通过相对路径 import knowledge_store/kb/evidence_contract
COPY . /app/

ENV WULI_HOST=0.0.0.0
ENV WULI_PORT=8000
# 公网部署必须显式挂载知识库，禁止默认暴露真实错题库
ENV WULI_LIBRARY=/data/library

EXPOSE 8000

CMD ["python", "deploy/mcp-server/mcp_server.py", "--http"]
