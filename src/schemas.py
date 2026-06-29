from pydantic import BaseModel
from fastapi_users import schemas
import uuid

class UserRead(schemas.BaseUser[uuid.UUID]):
    pass

class UserCreate(schemas.BaseUserCreate):
    pass

class UserUpdate(schemas.BaseUserUpdate):
    pass

class MessageCreate(BaseModel):

    content: str



class MessageResponse(BaseModel):

    id: uuid.UUID
    role: str
    content: str


    class Config:
        from_attributes = True



class ChatResponse(BaseModel):

    id: uuid.UUID
    title: str
    messages: list[MessageResponse]


    class Config:
        from_attributes = True