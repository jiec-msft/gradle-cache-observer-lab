package model;

import org.junit.Before;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;

public class SampleMathTest {
    @Before
    public void identifyRun() {
        System.out.println("NATIVE_CASE run=" + System.getProperty("native.runId"));
    }

    @Test
    public void doublesPositive() {
        assertEquals(6, SampleMath.twice(3));
    }

    @Test
    public void doublesZero() {
        assertEquals(0, SampleMath.twice(0));
    }

    @Test
    public void checksFailureBoundary() {
        assertFalse("Intentional native failure probe", Boolean.getBoolean("native.failTest"));
    }
}
