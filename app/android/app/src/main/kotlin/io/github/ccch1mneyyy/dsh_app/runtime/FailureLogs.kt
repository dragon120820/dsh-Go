package io.github.ccch1mneyyy.dsh_app.runtime

import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/** Binds a failure's persisted metadata to the exact masked output it describes. */
internal object FailureLogs {
    private const val LATEST = "failure-latest.log"
    private const val MAIN = "failure-normal.log"
    private const val LOG_SHA256 = "logSha256"

    class Record(val metadata: String, internal val bytes: ByteArray?)

    fun prepare(failure: RunFailure, tail: List<String>): Record {
        val bytes = try {
            tail.joinToString("\n", postfix = "\n") { LogRedactor.redact(it) }.toByteArray(Charsets.UTF_8)
        } catch (_: Exception) {
            null
        }
        // An empty digest means this capture failed, not a legacy record without a digest.
        val metadata = JSONObject(failure.toMap())
            .put(LOG_SHA256, bytes?.let(::sha256) ?: "").toString()
        return Record(metadata, bytes)
    }

    /** Best effort as before: a failed latest write may leave the main file from an earlier exit. */
    fun write(directory: File, record: Record, main: Boolean) {
        val bytes = record.bytes ?: return
        try {
            directory.mkdirs()
            File(directory, LATEST).writeBytes(bytes)
            if (main) File(directory, MAIN).writeBytes(bytes)
        } catch (_: Exception) {
        }
    }

    /** Metadata is captured with the failure before reading; never reread the file after hashing. */
    fun read(directory: File, main: Boolean, metadata: String?): List<String> {
        if (metadata == null) return emptyList()
        return try {
            val json = JSONObject(metadata)
            // Existing installations have no digest. Keep their old log-reading behavior.
            val expected = if (json.has(LOG_SHA256)) json.getString(LOG_SHA256) else null
            val bytes = File(directory, if (main) MAIN else LATEST).readBytes()
            if (expected != null && expected != sha256(bytes)) return emptyList()
            bytes.inputStream().bufferedReader(Charsets.UTF_8).use { it.readLines() }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { (it.toInt() and 0xff).toString(16).padStart(2, '0') }
}
