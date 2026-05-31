# Scripts
you need to add these to the `Printers` section

## Start G-code
```
M862.3 P "[printer_model]" ; printer model check
M862.1 P[nozzle_diameter] ; nozzle diameter check
M115 U3.14.1 ; tell printer latest fw version
G90 ; use absolute coordinates
M83 ; extruder relative mode
M104 S[first_layer_temperature] ; set extruder temp
M140 S[first_layer_bed_temperature] ; set bed temp
Tx
M190 S[first_layer_bed_temperature] ; wait for bed temp
M109 S[first_layer_temperature] ; wait for extruder temp
G28 W ; home all without mesh bed level
G80 X{first_layer_print_min[0]} Y{first_layer_print_min[1]} W{(first_layer_print_max[0]) - (first_layer_print_min[0])} H{(first_layer_print_max[1]) - (first_layer_print_min[1])} ; mesh bed levelling

;go outside print area
G1 Y-3 F1000
G1 Z0.4 F1000
; select extruder
Tc
; purge line
G1 X55 F2000
G1 Z0.3 F1000
G92 E0
G1 X240 E25 F2200
G1 Y-2 F1000
G1 X55 E25 F1400
G1 Z0.2 F1000
G1 X5 E4 F1000

M221 S{if layer_height<0.075}100{else}95{endif}
G92 E0

; Don't change E values below. Excessive value can damage the printer.
{if print_settings_id=~/.*(DETAIL @MK3|QUALITY @MK3).*/}M907 E430 ; set extruder motor current{endif}
{if print_settings_id=~/.*(SPEED @MK3|DRAFT @MK3).*/}M907 E538 ; set extruder motor current{endif}
; input_filename_base = [input_filename_base]
; printing_filament_types = [filament_type]
@CYCLETRON_COUNT = 5
@CYCLETRON_START 
;START_CYCLETRON
```


# End G-code
```
{if layer_z < max_print_height}G1 Z{z_offset+min(max_layer_z+1, max_print_height)} F720 ; Move print head up{endif}
G1 X0 Y210 F7200 ; park
{if layer_z < max_print_height}G1 Z{z_offset+min(max_layer_z+49, max_print_height)} F720 ; Move print head further up{endif}



G0 X0 Y200 F3000
G4 P250 ; wait
M104 S180 ; Set nozzle temp don't wait
M140 S0        ; Set bed temp to 0C, do not wait
M190 R30        ; Wait for bed to cool down to 30C
M300 S1000 P500 ; Beep (1000Hz for 500ms)
M300


G0 Z2 Y200 F3000 ; lower bar to ram position
G0 Y120 F12000 ; ram position
M104 S[first_layer_temperature] ; set extruder temp
M140 S[first_layer_bed_temperature] ; set bed temp
M190 S[first_layer_bed_temperature] ; wait for bed temp
M109 S[first_layer_temperature] ; wait for extruder temp
G28 Y ; home Y only after ramming

;END_CYCLETRON
@CYCLETRON_END 
```


# Print Settings > Post processing scripts

"C:\Python313\python.exe" "C:\Users\Powel\Documents\projects\git\cycletron\cycletron.py";

# Time
;PRINTING_TIME: [total seconds used]
;REMAINING_TIME: [seconds remaining]

; input_filename_base = [input_filename_base]