namespace Atlas.Domain;

public sealed record IdentificadorNota(string? Chave, int? Numero)
{
    public static IdentificadorNota? Parse(string? valor)
    {
        valor = valor?.Trim();
        if (string.IsNullOrEmpty(valor) || valor.Any(c => !char.IsAsciiDigit(c)))
            return null;

        if (valor.Length == 44 && valor[20..22] == "55")
            return new(valor, null);

        if (valor.Length <= 9 && int.TryParse(valor, out int numero) && numero > 0)
            return new(null, numero);

        return null;
    }
}
