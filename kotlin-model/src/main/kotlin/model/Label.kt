package model

object Label {
    fun format(value: String): String = "value:${Text.normalize(value)}"
}
