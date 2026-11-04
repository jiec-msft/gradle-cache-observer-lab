package model

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class LabelTest {
    @Test
    fun labelsNormalizedText() {
        assertEquals("value:sample", Label.format(" SAMPLE "))
    }

    @Test
    fun labelsEmptyText() {
        assertEquals("value:", Label.format(""))
    }
}
