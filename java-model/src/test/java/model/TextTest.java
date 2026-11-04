package model;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class TextTest {
    @Test
    void normalizesAscii() {
        assertEquals("sample", Text.normalize("  SAMPLE "));
    }

    @Test
    void preservesEmptyValue() {
        assertEquals("", Text.normalize(" "));
    }
}
