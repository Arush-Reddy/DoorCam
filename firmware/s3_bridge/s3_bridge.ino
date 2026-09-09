/*
 * High-Speed USB-UART Bridge with Immediate USB Flush for ACKs
 */

#define CAM_RX 44  // S3 RX pin -> connected to CAM U0T
#define CAM_TX 43  // S3 TX pin -> connected to CAM U0R

HardwareSerial CamSerial(1);

void setup() {
  Serial.begin(115200);
  Serial.setRxBufferSize(4096);
  Serial.setTxBufferSize(4096);
  
  CamSerial.setRxBufferSize(4096);
  CamSerial.setTxBufferSize(4096);
  CamSerial.begin(115200, SERIAL_8N1, CAM_RX, CAM_TX);
}

void loop() {
  while (Serial.available()) {
    CamSerial.write(Serial.read());
  }
  
  if (CamSerial.available()) {
    while (CamSerial.available()) {
      Serial.write(CamSerial.read());
    }
    Serial.flush(); // Instantly push ACK packets to the PC!
  }
}
