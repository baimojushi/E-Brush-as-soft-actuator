#pragma once
#include "FreeRTOS.h"
typedef void* QueueHandle_t;
inline QueueHandle_t xQueueCreate(int,int){return nullptr;}
inline int xQueueSend(QueueHandle_t,const void*,int){return pdTRUE;}
inline int xQueueReceive(QueueHandle_t,void*,int){return 0;}
