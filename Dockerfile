# start from an official Python base image
FROM python:3.12-slim

# set working directory
WORKDIR /app

# install uv package manager and build dependencies
RUN pip install --no-cache-dir uv>=0.10.9

# copy project metadata first for dependency installation
COPY pyproject.toml .

# use uv install to install dependencies into the environment
RUN uv install --no-dev

# copy application source
COPY . .

# expose default port
EXPOSE 8000

# run the FastAPI app with uvicorn via uv CLI
CMD ["uv", "run", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
