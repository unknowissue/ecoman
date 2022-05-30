import os
import xmlrpc.client
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, Request, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pymongo import MongoClient


class PowerStatus:
    ON: str = "on"
    OFF: str = "off"
    SUSPEND: str = "suspend"
    UNKNOWN: str = "unknown"


class ProcessResult:
    OK: int = "ok"
    NG: int = "ng"


# @dataclass
# class ResponseUpdatePowerStatus:
#     result: ProcessResult
#     request_status: PowerStatus
#     message: str


@dataclass
class RequestUpdatePowerStatus:
    status: Literal["on", "off", "suspend", "shutdown", "reset", "reboot"]


MONGO_USERNAME = os.getenv("MONGO_USERNAME", "")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "")
MONGO_DBNAME = os.getenv("MONGO_DBNAME", "ecoman")
MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
if MONGO_USERNAME == "":
    credential = ""
else:
    credential = MONGO_USERNAME + ":"
    if MONGO_PASSWORD:
        credential += MONGO_PASSWORD
    credential += "@"
MONGO_CONNECTION_STRING = f"mongodb://{credential}{MONGO_HOST}/"


client = MongoClient(MONGO_CONNECTION_STRING)
client.admin.command('ping')
db = client[MONGO_DBNAME]


app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
def page_top(request: Request):
    collection = db.get_collection("machines")
    response = list(collection.find({}, {'_id': 0}))
    result = sorted(response, key=lambda x: (x['esxi_node_name'], x['id']))
    return templates.TemplateResponse("top.html", {
        "title": "Top",
        "machines": result,
        "threshold": {
            "cpu": 5,
            "ram_mb": 8192,
            "storage_gb": 50,
        },
        "request": request
    })


@app.get("/create", response_class=HTMLResponse)
def page_create_vm(request: Request):
    return templates.TemplateResponse("create.html", {
        "title": "Create VM",
        "request": request
    })


@app.get("/machine/{esxi_node_name}/{machine_id}", response_class=HTMLResponse)
def page_read_vm_detail(esxi_node_name: str, machine_id: int, request: Request):
    collection = db.get_collection("machines")
    filter_ = {
        "esxi_node_name": esxi_node_name,
        "id": machine_id
    }
    machine = collection.find_one(filter_, {'_id': 0})

    return templates.TemplateResponse("detail.html", {
        "title": f"Detail: {esxi_node_name} {machine_id}",
        "machine": machine,
        "request": request
    })


@app.put("/v1/machine/{esxi_node_name}/{machine_id}/power")
def api_update_vm_power(esxi_node_name: str, machine_id: int,
                        power_status: RequestUpdatePowerStatus):
    power_state = jsonable_encoder(power_status)["status"]
    with xmlrpc.client.ServerProxy("http://localhost:8600/") as proxy:
        result = proxy.set_vm_power(esxi_node_name, machine_id, power_state)

    if result.get("result") == ProcessResult.NG:
        raise HTTPException(status_code=503, detail=result.get("message"))
    return result


# @dataclass
# class CreateMachineRequest:
#     """ Request schema for creating a virtual machine """
#     name: str
#     ram_mb: int
#     cpu_cores: int
#     storage_gb: int
#     network_port_group: str
#     esxi_nodename: str
#     comment: str
#     author: str


# @app.post("/v1/machine")
# def api_create_vm(machine_req_req: CreateMachineRequest):
#     # encode recieved request
#     machine_req_req_enc = jsonable_encoder(machine_req_req)
#     # validate and convert datamodel
#     machine_req: CreateMachineSpec = validate_machine_req(
#         machine_req=machine_req_req_enc)

#     # Create Virtual Machine
#     result: CreateMchineResult = create_vm(
#         machine_req=machine_req)
#     if result.status == model.ProcessResult.NG:  # failed
#         print("Fail to create VM:", result.message)
#     return result
