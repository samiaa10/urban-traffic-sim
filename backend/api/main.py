from fastapi import FastAPI


app = FastAPI(
    title="NORWICH//SIM API",
    description="Urban traffic simulation and routing API",
    version="0.1.0"
)


@app.get("/")
def root():
    return {
        "name": "NORWICH//SIM",
        "status": "online"
    }