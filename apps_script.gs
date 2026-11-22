function doGet(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("Расписание");
  
  if (!sheet) {
    return ContentService.createTextOutput(JSON.stringify({"status": "error", "message": "Лист Расписание не найден"}))
      .setMimeType(ContentService.MimeType.JSON);
  }
  
  var data = sheet.getDataRange().getValues();
  var freeSlots = [];
  
  // Начинаем с 1, чтобы пропустить заголовок в первой строке
  for (var i = 1; i < data.length; i++) {
    var slot = data[i][0]; // Колонка А
    var status = data[i][1]; // Колонка B
    
    // Если слот не пустой и статус не равен "Занято"
    if (slot && status !== "Занято") {
      freeSlots.push(slot.toString());
    }
  }
  
  return ContentService.createTextOutput(JSON.stringify({"status": "success", "slots": freeSlots}))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheetLeads = ss.getSheetByName("Записи");
  var sheetSchedule = ss.getSheetByName("Расписание");
  
  try {
    var data = JSON.parse(e.postData.contents);
    var rowData = [data.slot_time, data.full_name, data.phone, data.complaints, data.analyses, data.username];
    
    // 1. Добавляем лид на лист "Записи"
    sheetLeads.appendRow(rowData);
    
    // 2. Ищем этот слот на листе "Расписание" и ставим "Занято"
    if (sheetSchedule) {
      var schedData = sheetSchedule.getDataRange().getValues();
      for (var i = 1; i < schedData.length; i++) {
        if (schedData[i][0].toString() === data.slot_time) {
          // Ставим статус "Занято" во вторую колонку (B)
          sheetSchedule.getRange(i + 1, 2).setValue("Занято");
          break;
        }
      }
    }
    
    return ContentService.createTextOutput(JSON.stringify({"status": "success"}))
      .setMimeType(ContentService.MimeType.JSON);
      
  } catch (error) {
    return ContentService.createTextOutput(JSON.stringify({"status": "error", "message": error.toString()}))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
