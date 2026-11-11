package io.github.ccch1mneyyy.dsh_app.runtime

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.io.RandomAccessFile
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions

/** Actual file I/O through the store used by RuntimeController; no Android prefs/Handler execution. */
class FailureLogsTest {
    private fun failure(at: Long, safe: Boolean = false) = RunFailure(
        kind = FailureKind.STARTUP_FAILED, exitCode = 1, at = at, safeMode = safe,
        summary = "startup failed: fixture-$at", requiredRows = emptyList(), suspects = emptyList(),
        diagnostics = null, upSeconds = null,
    )

    private fun inDirectory(block: (File) -> Unit) {
        val directory = Files.createTempDirectory("failure-logs").toFile()
        try {
            block(directory)
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test fun successfulCaptureKeepsTheExistingNamesBytesAndMaskedOutput() = inDirectory { directory ->
        val tail = listOf("第一条", "token=" + "fixture", "")
        val record = FailureLogs.prepare(failure(1), tail)
        FailureLogs.write(directory, record, main = true)
        val expected = listOf("第一条", "token=•••", "")
        assertEquals(expected, FailureLogs.read(directory, false, record.metadata))
        assertEquals(expected, FailureLogs.read(directory, true, record.metadata))
        for (name in listOf("failure-latest.log", "failure-normal.log")) {
            assertArrayEquals("第一条\ntoken=•••\n\n".toByteArray(Charsets.UTF_8), File(directory, name).readBytes())
        }
        // The digest is private persistence metadata; the public failure map is unchanged.
        val metadata = JSONObject(record.metadata)
        assertEquals(64, metadata.getString("logSha256").length)
        assertEquals("startupFailed", metadata.getString("kind"))
        assertEquals(1L, metadata.getLong("at"))
        assertEquals(JSONObject(failure(1).toMap()).length() + 1, metadata.length())
        assertFalse(failure(1).toMap().containsKey("logSha256"))
    }

    @Test fun aSafeFailureUpdatesOnlyTheLatestLogAndKeepsTheMainCapture() = inDirectory { directory ->
        val main = FailureLogs.prepare(failure(1), listOf("main output"))
        FailureLogs.write(directory, main, main = true)
        val safe = FailureLogs.prepare(failure(2, safe = true), listOf("safe output"))
        FailureLogs.write(directory, safe, main = false)
        assertEquals(listOf("safe output"), FailureLogs.read(directory, false, safe.metadata))
        assertEquals(listOf("main output"), FailureLogs.read(directory, true, main.metadata))
    }

    @Test fun failedLatestOpenCannotAttachTheOldMainFileToNewMetadataAndRetryRecovers() = inDirectory { directory ->
        val old = FailureLogs.prepare(failure(1), listOf("earlier main output"))
        FailureLogs.write(directory, old, main = true)
        val latest = File(directory, "failure-latest.log")
        assertTrue(latest.delete())
        assertTrue(latest.mkdir()) // Real FileOutputStream open failure, without a write callback substitute.
        val next = FailureLogs.prepare(failure(2), listOf("new main output"))
        FailureLogs.write(directory, next, main = true)
        assertEquals("earlier main output\n", File(directory, "failure-normal.log").readText())
        assertEquals(listOf("earlier main output"), FailureLogs.read(directory, true, old.metadata))
        assertEquals(emptyList<String>(), FailureLogs.read(directory, true, next.metadata))
        assertEquals(emptyList<String>(), FailureLogs.read(directory, false, next.metadata))
        File(directory, "metadata.json").writeText(next.metadata)
        val reloaded = File(directory, "metadata.json").readText()
        assertEquals(emptyList<String>(), FailureLogs.read(directory, true, reloaded))

        assertTrue(latest.delete())
        FailureLogs.write(directory, next, main = true)
        assertEquals(listOf("new main output"), FailureLogs.read(directory, false, next.metadata))
        assertEquals(listOf("new main output"), FailureLogs.read(directory, true, next.metadata))
    }

    @Test fun mainWriteFailureIsRejectedWithLiveAndReloadedMetadata() = inDirectory { directory ->
        assumeTrue("POSIX permissions are required", Files.getFileStore(directory.toPath()).supportsFileAttributeView("posix"))
        val old = FailureLogs.prepare(failure(1), listOf("earlier main output"))
        FailureLogs.write(directory, old, main = true)
        val main = File(directory, "failure-normal.log")
        val permissions = Files.getPosixFilePermissions(main.toPath())
        try {
            Files.setPosixFilePermissions(main.toPath(), PosixFilePermissions.fromString("r--------"))
            assumeTrue("Run without a permission-bypassing uid/capability", !main.canWrite())
            val next = FailureLogs.prepare(failure(2), listOf("new main output"))
            FailureLogs.write(directory, next, main = true)
            // The latest write succeeded; it is the second file's own open that failed.
            assertEquals(listOf("new main output"), FailureLogs.read(directory, false, next.metadata))
            assertEquals("earlier main output\n", main.readText())
            assertEquals(emptyList<String>(), FailureLogs.read(directory, true, next.metadata))
            File(directory, "metadata.json").writeText(next.metadata)
            val reloaded = File(directory, "metadata.json").readText()
            assertEquals(emptyList<String>(), FailureLogs.read(directory, true, reloaded))
        } finally {
            Files.setPosixFilePermissions(main.toPath(), permissions)
        }
    }

    @Test fun linuxNoSpaceOnLatestWriteDoesNotExposeTheOldMainAsTheNewFailure() {
        // /dev/full supplies the real ENOSPC write result without filling the host disk.
        // This is an I/O fault fixture, not a claim that Android deploys this log as a symlink.
        assumeTrue("Linux CI supplies /dev/full", File("/dev/full").exists())
        inDirectory { directory ->
            val old = FailureLogs.prepare(failure(1), listOf("earlier main output"))
            FailureLogs.write(directory, old, main = true)
            val latest = File(directory, "failure-latest.log")
            assertTrue(latest.delete())
            Files.createSymbolicLink(latest.toPath(), File("/dev/full").toPath())
            val next = FailureLogs.prepare(failure(2), listOf("new main output"))
            FailureLogs.write(directory, next, main = true)
            assertEquals("earlier main output\n", File(directory, "failure-normal.log").readText())
            assertEquals(emptyList<String>(), FailureLogs.read(directory, true, next.metadata))
            // Do not read /dev/full: only the unchanged, regular main log is under test here.
        }
    }

    @Test fun aPartialFileIsRejectedUsingMetadataReadBackFromDisk() = inDirectory { directory ->
        val record = FailureLogs.prepare(failure(1), listOf("first line", "second line"))
        FailureLogs.write(directory, record, main = true)
        val saved = File(directory, "metadata.json").apply { writeText(record.metadata) }
        RandomAccessFile(File(directory, "failure-normal.log"), "rw").use { it.setLength(5) }
        assertEquals(emptyList<String>(), FailureLogs.read(directory, true, saved.readText()))
        assertEquals(listOf("first line", "second line"), FailureLogs.read(directory, false, saved.readText()))
    }

    @Test fun oldMetadataCannotReadACompletelyWrittenNewLog() = inDirectory { directory ->
        val old = FailureLogs.prepare(failure(1), listOf("earlier output"))
        FailureLogs.write(directory, old, main = true)
        val next = FailureLogs.prepare(failure(2), listOf("later output"))
        FailureLogs.write(directory, next, main = true)
        assertEquals(emptyList<String>(), FailureLogs.read(directory, false, old.metadata))
        assertEquals(emptyList<String>(), FailureLogs.read(directory, true, old.metadata))
        assertEquals(listOf("later output"), FailureLogs.read(directory, true, next.metadata))
    }

    @Test fun legacyMetadataKeepsTheExistingReadBehaviorAndInvalidMetadataCannotSelectALog() = inDirectory { directory ->
        File(directory, "failure-normal.log").writeText("old installation output\n")
        val legacy = JSONObject(failure(1).toMap()).toString()
        assertEquals(listOf("old installation output"), FailureLogs.read(directory, true, legacy))
        assertEquals(emptyList<String>(), FailureLogs.read(directory, true, null))
        assertEquals(emptyList<String>(), FailureLogs.read(directory, true, "{"))
        val unavailable = JSONObject(legacy).put("logSha256", "").toString()
        assertEquals(emptyList<String>(), FailureLogs.read(directory, true, unavailable))
    }
}
