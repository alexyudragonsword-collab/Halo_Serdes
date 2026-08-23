package com.halo.serdes.probe.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions

/**
 * One parameter, rendered from its schema entry.
 *
 * The widget follows `kind`, so the config schema alone decides what the phone
 * shows. `path` is never displayed: it is the wire key, and the schema already
 * carries a human label with its unit.
 */
@Composable
fun FieldRow(
    field: FormField,
    value: Any?,
    error: String?,
    onChange: (Any) -> Unit,
) {
    when {
        field.isBool -> Row(
            Modifier.fillMaxWidth().padding(vertical = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(field.label, style = MaterialTheme.typography.bodyMedium)
            Switch(checked = value == true, onCheckedChange = onChange)
        }

        field.kind == "enum" -> EnumField(field, value?.toString().orEmpty(), onChange)

        else -> OutlinedTextField(
            value = value?.toString().orEmpty(),
            onValueChange = onChange,
            label = { Text(field.label) },
            isError = error != null,
            supportingText = when {
                error != null -> ({ Text(error) })
                field.optional -> ({ Text("optional — blank means unset") })
                else -> null
            },
            singleLine = true,
            textStyle = MaterialTheme.typography.bodyMedium
                .copy(fontFamily = FontFamily.Monospace),
            keyboardOptions = KeyboardOptions(keyboardType = field.keyboardType()),
            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        )
    }
}

/**
 * Integers get the number pad; everything else gets the full keyboard.
 *
 * Deliberately not Decimal for floats: engineering values here are routinely
 * entered as `2.4e-4`, and neither the number nor the decimal pad has an `e`.
 * A tidier keyboard that makes a legitimate value untypeable is a bad trade.
 * Tuple fields are comma-separated lists, so they need text too.
 */
private fun FormField.keyboardType(): KeyboardType =
    if (kind == "int") KeyboardType.Number else KeyboardType.Text

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EnumField(field: FormField, value: String, onChange: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = open,
        onExpandedChange = { open = !open },
        modifier = Modifier.padding(vertical = 4.dp),
    ) {
        OutlinedTextField(
            value = value,
            onValueChange = {},
            readOnly = true,
            label = { Text(field.label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = open) },
            modifier = Modifier
                .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                .fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            field.options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option) },
                    onClick = { open = false; onChange(option) },
                )
            }
        }
    }
}

/** A collapsed group of parameters. */
@Composable
fun FormSectionBody(
    section: FormSection,
    values: Map<String, Any>,
    errors: Map<String, String>,
    onChange: (String, Any) -> Unit,
) = Column(Modifier.fillMaxWidth()) {
    section.fields.forEach { f ->
        FieldRow(f, values[f.path], errors[f.path]) { onChange(f.path, it) }
    }
}
