"use strict";

const addRoom = document.getElementById("add-room");
const roomTotal = document.getElementById("id_rooms-TOTAL_FORMS");
const roomTemplate = document.getElementById("empty-room-row");
const roomRows = document.getElementById("room-rows");

if (addRoom && roomTotal && roomTemplate && roomRows) {
  addRoom.disabled = Number(roomTotal.value) >= 100;
  addRoom.addEventListener("click", () => {
    const index = Number(roomTotal.value);
    if (index >= 100) return;
    const row = roomTemplate.content.cloneNode(true);
    row.querySelectorAll("[id], [name], [for]").forEach((element) => {
      for (const attribute of ["id", "name", "for"]) {
        const value = element.getAttribute(attribute);
        if (value) element.setAttribute(attribute, value.replaceAll("__prefix__", String(index)));
      }
    });
    const input = row.querySelector('input[name$="-room"]');
    roomRows.append(row);
    roomTotal.value = String(index + 1);
    addRoom.disabled = index + 1 >= 100;
    input.focus();
  });
}