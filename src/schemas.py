from pydantic import BaseModel
import uuid

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

class ChatRequest(BaseModel):
    id: uuid.UUID
    message: str
    model: str