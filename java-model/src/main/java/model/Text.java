package model;

import java.util.Locale;
import java.util.Objects;

public final class Text {
    private Text() {}

    public static String normalize(String value) {
        return Objects.requireNonNull(value).trim().toLowerCase(Locale.ROOT);
    }
}
